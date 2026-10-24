"""HTTP API for document upload, session control, and LiveKit tokens."""
import os
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from livekit import api as lk_api

from db_driver import init_db, recent_interview, save_interview
from interview import accept_consent, create_interview, end_interview, get_session, read_document, save_answer
from prompts import CONSENT_NOTICE

load_dotenv()
init_db()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 9 * 1024 * 1024
CORS(app, resources={r"/*": {"origins": os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")}})


def response_session(session):
    """Remove private document contents before returning a session to the browser."""
    return {k: v for k, v in session.items() if k not in ("resume", "job_description", "chunks")}


@app.get("/health")
def health():
    """Report API status and whether model and voice credentials are configured."""
    return jsonify({"status": "ok", "voice_configured": bool(os.getenv("LIVEKIT_URL") and os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_API_SECRET")), "groq_configured": bool(os.getenv("GROQ_API_KEY"))})


@app.post("/interviews")
def create():
    """Read uploaded or pasted documents and create a saved interview."""
    try:
        resume = read_document(request.files.get("resume"), request.form.get("resume_text", ""))
        jd = read_document(request.files.get("job_description"), request.form.get("job_description_text", ""))
        if not resume and not jd:
            return jsonify({"error": "Upload or paste a resume and/or job description first."}), 400
        return jsonify(response_session(create_interview(resume, jd)))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/interviews/recent")
def recover():
    """Return the most recently updated interview for recovery."""
    session = recent_interview()
    return (jsonify(response_session(session)), 200) if session else (jsonify({"error": "No saved interview yet."}), 404)


@app.get("/interviews/<session_id>")
def get_interview(session_id):
    """Return one saved interview by its ID."""
    session = get_session(session_id)
    return (jsonify(response_session(session)), 200) if session else (jsonify({"error": "Interview not found."}), 404)


@app.post("/interviews/<session_id>/consent")
def consent(session_id):
    """Record consent and prepare or resume the interview questions."""
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Interview not found."}), 404
    data = request.get_json(silent=True) or {}
    if session.get("status") == "active" and data.get("consent"):
        session["consent"] = True
        save_interview(session)
    else:
        session = accept_consent(session, bool(data.get("consent")))
    out = response_session(session)
    out["consent_notice"] = CONSENT_NOTICE
    return jsonify(out)


@app.post("/interviews/<session_id>/answer")
def answer(session_id):
    """Save an answer and return any follow-up or next question."""
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Interview not found."}), 404
    data = request.get_json(silent=True) or {}
    try:
        result = save_answer(session, data.get("answer", ""), data.get("source", "text"), data.get("duration_seconds"))
        return jsonify({"result": result, "session": response_session(session)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/interviews/<session_id>/end")
def finish(session_id):
    """End an interview and generate its feedback report."""
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Interview not found."}), 404
    report = end_interview(session)
    return jsonify({"report": report, "session": response_session(session)})


@app.get("/interviews/<session_id>/report")
def report(session_id):
    """Return the saved feedback report for an interview."""
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Interview not found."}), 404
    return jsonify(session.get("report") or {"error": "Interview has not ended yet."})


@app.get("/getToken")
def get_token():
    """Issue a LiveKit room token only for an active consented interview."""
    required = ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    if any(not os.getenv(key) for key in required):
        return jsonify({"error": "LiveKit credentials are not configured on the backend."}), 503
    interview_id = request.args.get("interview_id", "")
    session = get_session(interview_id)
    if not session or not session.get("consent") or session.get("status") != "active":
        return jsonify({"error": "A consented active interview is required."}), 403
    room = "interview-" + interview_id
    name = "candidate-" + uuid.uuid4().hex[:8]
    token = lk_api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
        .with_identity(name).with_name("Candidate") \
        .with_grants(lk_api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
    return jsonify({"token": token.to_jwt(), "url": os.getenv("LIVEKIT_URL"), "room": room})


@app.errorhandler(413)
def too_large(_error):
    """Return a clear error when an upload exceeds the request size limit."""
    return jsonify({"error": "Combined upload limit is 9 MB."}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=False)
