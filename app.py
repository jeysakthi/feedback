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
feedback_store = []  # In-memory feedback list
user_feedback_state = {}  # Store rating/comments per user

# ---------------------------
# Verify Slack request
# ---------------------------
def verify_slack_request(request: Request, body: str):
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")
    if abs(time.time() - int(timestamp)) > 60 * 5:
        print("❌ Timestamp too old!")
        return False
    sig_basestring = f"v0:{timestamp}:{body}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()
    print(f"🔍 Calculated Signature: {my_signature}")
    return hmac.compare_digest(my_signature, slack_signature)

# ---------------------------
# Slack API helpers
# ---------------------------
def get_user_name(user_id):
    url = "https://slack.com/api/users.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"user": user_id}
    resp = requests.get(url, headers=headers, params=params).json()
    print(f"✅ Fetched user name for {user_id}: {resp}")
    return resp.get("user", {}).get("real_name", "Unknown")

def get_channel_name(channel_id):
    url = "https://slack.com/api/conversations.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": channel_id}
    resp = requests.get(url, headers=headers, params=params).json()
    print(f"✅ Fetched channel name for {channel_id}: {resp}")
    return resp.get("channel", {}).get("name", "Unknown")

def send_slack_message(url, payload):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload)
    print(f"✅ Slack API Response: {resp.status_code}, {resp.text}")
    return resp.json()

# ---------------------------
# Feedback endpoints
# ---------------------------
@app.post("/feedback")
async def receive_feedback(request: Request):
    data = await request.json()
    feedback_store.append(data)  # Store in memory only
    print("✅ Feedback stored in memory:", data)
    return {"status": "success", "received": data}

@app.get("/feedback")
async def get_feedback():
    print(f"✅ Returning feedback list: {feedback_store}")
    return {"feedback": feedback_store}

# ---------------------------
# Slack Events endpoint
# ---------------------------
@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    body_str = body.decode()
    if not verify_slack_request(request, body_str):
        print("❌ Invalid Slack signature!")
        return {"error": "invalid signature"}

    data = json.loads(body_str)
    print("🔍 Full Slack Event Payload:", json.dumps(data, indent=2))

    if data.get("type") == "url_verification":
        print("✅ URL verification challenge received.")
        return {"challenge": data["challenge"]}

    if data.get("type") == "event_callback":
        event = data.get("event", {})
        print(f"✅ Event Type: {event.get('type')}, Text: {event.get('text')}")
        if event.get("type") == "message" and "subtype" not in event:
            user_text = event.get("text", "")
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))

            if "This issue is resolved" in user_text:
                print("✅ Trigger phrase detected, sending Yes button...")
                send_yes_button(channel_id, thread_ts)

    return {"status": "ok"}

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
    print(f"✅ Sending Yes button to channel {channel}, thread {thread_ts}")
    send_slack_message(url, payload)

# ---------------------------
# Send form message and capture ts
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
                "text": {"type": "mrkdwn", "text": "*Rate your experience (1-5):*"},
                "accessory": {
                    "type": "static_select",
                    "action_id": "rating_select",
                    "placeholder": {"type": "plain_text", "text": "Select a rating"},
                    "options": [{"text": {"type": "plain_text", "text": str(i)}, "value": str(i)} for i in range(1, 6)]
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
    print(f"✅ Sending feedback form to channel {channel}, thread {thread_ts}")
    resp = send_slack_message(url, payload)
    form_ts = resp.get("ts")
    if user_id and form_ts:
        user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
        user_feedback_state[user_id]["form_ts"] = form_ts
        print(f"✅ Captured form message ts: {form_ts}")

# ---------------------------
# Update form & thank you
# ---------------------------
def update_feedback_form(channel, ts):
    url = "https://slack.com/api/chat.update"
    payload = {
        "channel": channel,
        "ts": ts,
        "text": "Feedback submitted ✅",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Thank you! Your feedback has been recorded."}
            }
        ]
    }
    print(f"✅ Updating feedback form for channel {channel}, ts {ts}")
    send_slack_message(url, payload)

def send_thank_you(channel, thread_ts):
    url = "https://slack.com/api/chat.postMessage"
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": "✅ Thank you for your feedback!"
    }
    print(f"✅ Sending thank-you message to channel {channel}, thread {thread_ts}")
    send_slack_message(url, payload)

# ---------------------------
# Interactivity endpoint
# ---------------------------
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    try:
        print("✅ Received interactivity request")
        form_data = await request.form()
        print("🔍 Raw form data:", form_data)
        payload = form_data.get("payload")
        if not payload:
            print("❌ No payload found in request.")
            return {"error": "Missing payload"}

        data = json.loads(payload)
        print("🔍 Full Interactivity Payload:", json.dumps(data, indent=2))

        if data.get("type") == "block_actions":
            action_id = data["actions"][0].get("action_id")
            print(f"✅ Action ID: {action_id}")

            if action_id == "show_feedback_form":
                channel_id = data.get("channel", {}).get("id")
                thread_ts = data.get("container", {}).get("thread_ts") or data.get("container", {}).get("message_ts")
                user_id = data.get("user", {}).get("id")
                print(f"✅ Yes button clicked. Channel: {channel_id}, Thread TS: {thread_ts}")
                send_feedback_form(channel_id, thread_ts, user_id)

            elif action_id == "rating_select":
                user_id = data.get("user", {}).get("id")
                rating = data["actions"][0].get("selected_option", {}).get("value")
                if user_id and rating:
                    user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
                    user_feedback_state[user_id]["rating"] = rating
                    print(f"✅ Rating selected: {rating}")

            elif action_id == "feedback_text":
                user_id = data.get("user", {}).get("id")
                feedback_text = data["actions"][0].get("value", "")
                if user_id:
                    user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
                    user_feedback_state[user_id]["comments"] = feedback_text
                    print(f"✅ Feedback text entered: {feedback_text}")

            elif action_id == "submit_feedback":
                user_id = data.get("user", {}).get("id")
                channel_id = data.get("channel", {}).get("id")
                thread_ts = data.get("container", {}).get("thread_ts") or data.get("container", {}).get("message_ts")
                state = user_feedback_state.get(user_id, {})

                if state.get("submitted"):
                    print("❌ Duplicate submission detected!")
                    return {"text": "You have already submitted feedback for this thread."}

                rating = state.get("rating")
                # comments = state.get("comments", "")
                comments = ""
                state_values = data.get("state", {}).get("values", {})
                if "feedback_block" in state_values:
                    comments = state_values["feedback_block"]["feedback_text"].get("value", "")
                if not rating:
                    print("❌ Rating missing!")
                    return {"text": "Please select a rating before submitting."}

                state["submitted"] = True

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
                print("✅ Final Feedback Data:", feedback_data)

                # Store in memory and send to external API
                feedback_store.append(feedback_data)
                requests.post("https://feedback-jeysakthi1140-p6js52a9.leapcell.dev/feedback", json=feedback_data)

                # Update form using captured ts
                form_ts = state.get("form_ts")
                if form_ts:
                    update_feedback_form(channel_id, form_ts)
                else:
                    print("❌ No form_ts found, cannot update form message!")

                send_thank_you(channel_id, thread_ts)

                return {"text": "Thank you for your feedback!"}

        return {"status": "ok"}

    except Exception as e:
        print("❌ Exception in /slack/interactivity:", str(e))
        return {"error": "Internal Server Error"}

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
