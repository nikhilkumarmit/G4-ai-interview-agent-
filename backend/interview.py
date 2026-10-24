"""Interview workflow, grounded question selection, rubric, and document helpers."""
from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone

from db_driver import load_interview, save_interview
from prompts import PROHIBITED_PERSONAL_QUESTION

STAGES = ["intro", "resume", "behavioral", "role_specific", "candidate_questions", "closing"]
MAX_QUESTIONS = int(os.getenv("MAX_QUESTIONS", "8"))
MAX_MINUTES = int(os.getenv("MAX_INTERVIEW_MINUTES", "30"))
VAGUE = {"yes", "no", "maybe", "i don't know", "idk", "not sure", "nothing", "n/a", "ok", "okay"}
PERSONAL = re.compile(r"\b(age|how old|marital|married|children|pregnan|religion|church|race|ethnic|nationality|citizenship|disability|medical condition|sexual orientation|gender identity)\b", re.I)
PERSONAL_REQUEST = re.compile(r"(?i)\b(?:ask me|ask them|could you ask|include a question|question about)\b.{0,100}\b(age|how old|marital|married|children|pregnan|religion|church|race|ethnic|nationality|citizenship|disability|medical condition|sexual orientation|gender identity)\b")
INJECTION = re.compile(r"(?i)\b(ignore|disregard|override|forget)\b.{0,100}\b(instructions?|rules?|system|prompt|developer|safety|previous)\b[^\n.]*[.]?")
INJECTION_LINE = re.compile(r"(?im)^.*\b(reveal|expose|print|show|share|dump)\b.{0,80}\b(secrets?|credentials?|api keys?|tokens?)\b.*(?:\n|$)")


def _without_instructions(text):
    """Remove common embedded instructions from document text."""
    return INJECTION_LINE.sub("", INJECTION.sub("", text))


def _extract_pdf(file_storage):
    """Extract text from a PDF upload using an installed lightweight parser."""
    raw = file_storage.read()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("PDF must be smaller than 8 MB.")
    try:
        import pymupdf
        doc = pymupdf.open(stream=raw, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    except ImportError:
        try:
            from pypdf import PdfReader
            import io
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        except ImportError as exc:
            raise RuntimeError("PDF support is unavailable. Install the backend requirements.") from exc


def read_document(file_storage, pasted_text=""):
    """Read a PDF, text file, or pasted text and enforce document size limits."""
    if not file_storage or not file_storage.filename:
        text = (pasted_text or "").strip()
    else:
        name = file_storage.filename.lower()
        if name.endswith(".pdf"):
            text = _extract_pdf(file_storage)
        elif name.endswith(".txt"):
            raw = file_storage.read()
            if len(raw) > 512 * 1024:
                raise ValueError("Text document must be smaller than 512 KB.")
            text = raw.decode("utf-8", errors="replace")
        else:
            raise ValueError("Upload a PDF or TXT file, or paste text.")
        text = (text + "\n" + (pasted_text or "")).strip()
    if len(text) > 80_000:
        text = text[:80_000]
    return text


def chunks(text, kind):
    """Split document text into small chunks with section labels."""
    parts = re.split(r"\n\s*\n|(?m)^(?=[A-Z][A-Z /&-]{2,30}:?$)", text)
    result = []
    for section_index, part in enumerate(filter(str.strip, parts)):
        section = part.splitlines()[0][:80] if "\n" in part else "General"
        for offset in range(0, len(part), 900):
            result.append({"type": kind, "section": section, "text": part[offset:offset + 900]})
    return result


def summarize(resume, jd):
    """Find basic candidate skills, project details, experience, and target role."""
    resume = _without_instructions(resume)
    jd = _without_instructions(jd)
    skills = []
    skill_line = re.search(r"(?im)^\s*(?:technical\s+)?skills?\s*:?\s*(.+)$", resume)
    if skill_line:
        skills = [x.strip(" .•-\t") for x in re.split(r"[,|•;]", skill_line.group(1)) if len(x.strip()) > 1][:12]
    if not skills:
        candidates = re.findall(r"\b(Python|Java|JavaScript|TypeScript|React|SQL|AWS|Docker|Kubernetes|C\+\+|Machine Learning|Data Analysis|Flask|Node\.js|Git)\b", resume, re.I)
        skills = list(dict.fromkeys(candidates))[:12]
    lines = [re.sub(r"\s+", " ", s).strip(" •-") for s in resume.splitlines() if len(s.strip()) > 35]
    projects = [s for s in lines if re.search(r"(?i)project|built|developed|implemented|created", s)][:5]
    experience = [s for s in lines if re.search(r"(?i)\b(intern|engineer|developer|analyst|manager|experience|worked)\b", s)][:5]
    role = re.search(r"(?im)^\s*(?:job title|role|position)\s*:\s*(.+)$", jd)
    target_role = role.group(1).strip() if role else next((re.sub(r"\s+", " ", s).strip() for s in jd.splitlines() if re.search(r"(?i)\b(engineer|developer|analyst|designer|intern|manager)\b", s)), "Role described in job description")
    return {"candidate_name": _candidate_name(resume), "skills": skills, "projects": projects, "experience": experience, "target_role": target_role[:120], "resume_chars": len(resume), "jd_chars": len(jd)}


def _candidate_name(text):
    """Guess a candidate name from the first resume line when possible."""
    first = next((s.strip() for s in text.splitlines() if s.strip()), "")
    if first and len(first) < 70 and re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", first) and len(first.split()) <= 4:
        return first
    return "Candidate name not detected"


def create_interview(resume, jd):
    """Create and save a prepared interview from resume and job text."""
    resume = resume.strip()
    jd = jd.strip()
    summary = summarize(resume, jd)
    session = {
        "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "status": "prepared",
        "stage": "intro", "consent": False, "resume": resume, "job_description": jd, "summary": summary,
        "chunks": chunks(resume, "resume") + chunks(jd, "job_description"), "questions": [], "asked_questions": [],
        "answers": [], "followups": [], "notes": [], "elapsed_seconds": 0, "time_limit_seconds": MAX_MINUTES * 60,
        "question_budget": MAX_QUESTIONS, "started_epoch": None, "current_question": None, "report": None,
        "voice_metrics": {"response_latency_seconds": []},
    }
    save_interview(session)
    return session


def _grounded_questions(session):
    """Build interview questions from resume and job-description evidence."""
    summary = session["summary"]
    resume = session["resume"]
    jd = session["job_description"]
    result = []
    # Questions refer only to explicit document evidence. If evidence is sparse, say so.
    for project in summary["projects"][:3]:
        evidence = project[:240]
        result.append({"stage": "resume", "text": f"Your resume mentions: ‘{evidence}’. What was your specific contribution, and what result did you achieve?", "evidence": evidence})
    for skill in summary["skills"][:max(0, 3 - len(result))]:
        result.append({"stage": "resume", "text": f"Your resume lists {skill}. Can you describe one specific time you used it and the result?", "evidence": skill})
    if len(result) < 3 and resume:
        excerpt = re.sub(r"\s+", " ", _without_instructions(resume))[:180]
        result.append({"stage": "resume", "text": f"I have limited structured detail from your resume. Could you walk me through this detail and your own contribution: ‘{excerpt}’?", "evidence": excerpt})
    if not resume:
        result = [{"stage": "resume", "text": "I don’t have a resume to reference yet. What is one project or experience you’d like to discuss?", "evidence": "No resume provided"}]
    jd = _without_instructions(jd)
    jd_evidence = re.sub(r"\s+", " ", jd)[:240] if jd else "No job description provided"
    resume_evidence = (summary["skills"][0] if summary["skills"] else (summary["projects"][0][:100] if summary["projects"] else "No matching resume detail detected"))
    result.append({"stage": "resume", "text": f"The job description says ‘{jd_evidence}’. Which part of your background, including ‘{resume_evidence}’, best prepares you for this role?", "evidence": f"JD: {jd_evidence}; resume: {resume_evidence}"})
    result.extend([
        {"stage": "behavioral", "text": "Tell me about a challenging goal you worked toward. What was the situation, what actions did you take, and what was the outcome?", "evidence": "STAR behavioral question"},
        {"stage": "role_specific", "text": f"For the {summary['target_role']} role, how would you approach a new task where the requirements were unclear?", "evidence": summary["target_role"]},
        {"stage": "role_specific", "text": "Describe a time you received feedback and changed your approach. What changed afterward?", "evidence": "Role-related communication"},
    ])
    return result[:session["question_budget"]]


def accept_consent(session, consent):
    """Start question flow only after the candidate agrees to data processing."""
    if not consent:
        session["status"] = "consent_declined"
        save_interview(session)
        return session
    session["consent"] = True
    session["status"] = "active"
    session["started_epoch"] = time.time()
    session["stage"] = "resume"
    session["questions"] = _grounded_questions(session)
    save_interview(session)
    next_question(session)
    return session


def next_question(session):
    """Advance to the next prepared, grounded interview question."""
    if session["status"] != "active":
        return None
    elapsed = int(time.time() - (session["started_epoch"] or time.time()))
    session["elapsed_seconds"] = elapsed
    if elapsed >= session["time_limit_seconds"] or len(session["asked_questions"]) >= session["question_budget"]:
        session["stage"] = "candidate_questions"
        session["current_question"] = {"stage": "candidate_questions", "text": "What questions do you have about the role or interview practice?", "evidence": "Candidate questions"}
    elif session["questions"]:
        session["current_question"] = session["questions"].pop(0)
        session["current_question_id"] = str(uuid.uuid4())
        session["asked_questions"].append(session["current_question"]["text"])
        session["stage"] = session["current_question"]["stage"]
    else:
        session["stage"] = "candidate_questions"
        session["current_question"] = {"stage": "candidate_questions", "text": "What questions do you have about the role or interview practice?", "evidence": "Candidate questions"}
    save_interview(session)
    return session["current_question"]


def _completeness(answer):
    """Check whether an answer includes enough detail, action, and result."""
    words = re.findall(r"\b[\w'-]+\b", answer)
    has_action = bool(re.search(r"(?i)\b(i|we)\s+(built|made|led|used|designed|tested|analyzed|implemented|improved|created|decided|learned|communicated|worked|solved|managed|wrote|developed)\b", answer))
    has_result = bool(re.search(r"(?i)\b(result|outcome|impact|improved|reduced|increased|learned|achieved|delivered|because|so that|which)\b", answer))
    return len(words) >= 18 and has_action and has_result


def save_answer(session, answer, source="text", duration_seconds=None):
    """Record a turn, request up to two follow-ups, then advance."""
    answer = (answer or "").strip()[:6000]
    if not session.get("consent") or session["status"] != "active":
        raise ValueError("Interview is not active with consent.")
    if not answer:
        raise ValueError("Answer cannot be empty.")
    if PERSONAL.search(session.get("current_question", {}).get("text", "")):
        return {"refused": True, "message": PROHIBITED_PERSONAL_QUESTION}
    if PERSONAL_REQUEST.search(answer):
        return {"refused": True, "message": PROHIBITED_PERSONAL_QUESTION}
    if any(row.get("question_id") == question_id and row.get("answer") == answer for row in session["answers"]):
        if session.get("current_question", {}).get("is_followup"):
            return {"followup": session["current_question"]["text"], "advanced": False, "duplicate": True}
        return {"followup": None, "advanced": True, "next_question": session.get("current_question"), "duplicate": True}
    q = session.get("current_question") or {}
    question_id = session.get("current_question_id") or str(len(session["answers"]))
    previous_followups = sum(1 for f in session["followups"] if f["question_id"] == question_id)
    turn = {"question": q.get("text", ""), "question_id": question_id, "answer": answer, "source": source, "duration_seconds": duration_seconds, "timestamp": datetime.now(timezone.utc).isoformat()}
    session["answers"].append(turn)
    if PERSONAL.search(answer):
        session["notes"].append("Candidate requested a prohibited personal question; redirected to role-related discussion.")
    vague = len(answer.split()) < 12 or answer.casefold().strip(" .!?") in VAGUE or not _completeness(answer)
    if vague and previous_followups < 2:
        followup = {"question_id": question_id, "text": "Could you add a specific action you took and the result or what you learned?", "followup_number": previous_followups + 1}
        session["followups"].append(followup)
        session["current_question"] = {**q, "text": followup["text"], "is_followup": True}
        save_interview(session)
        return {"followup": followup["text"], "advanced": False}
    session["current_question_id"] = str(uuid.uuid4())
    current_index = len(session["asked_questions"])
    if current_index >= 4:
        session["stage"] = "behavioral" if current_index == 4 else "role_specific"
    nxt = next_question(session)
    save_interview(session)
    return {"followup": None, "advanced": True, "next_question": nxt}


def rubric_score(answer, question=""):
    """Give an answer heuristic 1–5 scores across the practice rubric."""
    words = re.findall(r"\b[\w'-]+\b", answer)
    complete = _completeness(answer)
    specificity = 3 if re.search(r"\d|percent|%|specific|customer|project|team|user|api|database", answer, re.I) else (2 if len(words) > 20 else 1)
    structure = 4 if complete else (3 if len(words) > 35 else 2)
    communication = 4 if 15 <= len(words) <= 180 else 3
    q_terms = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", question)}
    a_terms = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", answer)}
    overlap = len(q_terms & a_terms) / max(1, min(8, len(q_terms)))
    relevance = 4 if overlap >= .25 else 3 if overlap > 0 else 2
    return {"relevance": relevance, "structure": structure, "specificity": specificity, "communication": communication}


def build_report(session):
    """Score recorded answers and save an evidence-linked feedback report."""
    answers = session["answers"]
    scored = []
    for row in answers:
        scores = rubric_score(row["answer"], row.get("question", ""))
        quote = row["answer"][:240]
        scored.append({**row, "scores": scores, "evidence": quote, "average": round(sum(scores.values()) / 4, 2)})
    all_text = " ".join(a["answer"] for a in answers)
    words = re.findall(r"\b[\w'-]+\b", all_text)
    seconds = sum(a.get("duration_seconds") or 0 for a in answers)
    filler_terms = re.findall(r"\b(?:um+|uh+|like|you know|actually|basically)\b", all_text, re.I)
    average = round(sum(a["average"] for a in scored) / len(scored), 2) if scored else None
    report = {
        "scores": scored, "overall": average, "rubric": {"scale": "1–5", "dimensions": ["relevance", "structure", "specificity", "communication"], "note": "Evidence-based practice feedback only; not a hiring decision."},
        "strengths": [f"Your {name.lower()} score reached {value}/5 for “{quote[:110]}”." for name, value, quote in (sorted([(dimension.title(), max((a["scores"][dimension] for a in scored), default=0), next((a["evidence"] for a in scored if a["scores"][dimension] == max((x["scores"][dimension] for x in scored), default=0)), "No transcript evidence")) for dimension in ("relevance", "structure", "specificity", "communication")], key=lambda x: x[1], reverse=True)[:3]) if scored] or ["No answer evidence was recorded, so strengths cannot yet be assessed."],
        "improvements": ["Add a clear situation, action, and result to behavioral answers.", "Use concrete examples, outcomes, or measurements where available.", "Review the transcript for recognition errors before relying on the scores."],
        "metrics": {"words_per_minute": round(60 * len(words) / seconds, 1) if seconds else None, "long_pauses": None, "filler_word_count": len(filler_terms) if all(a.get("source") == "voice" for a in answers) else None, "average_response_latency_seconds": round(sum(session.get("voice_metrics", {}).get("response_latency_seconds", [])) / len(session.get("voice_metrics", {}).get("response_latency_seconds", [])), 3) if session.get("voice_metrics", {}).get("response_latency_seconds") else None, "unavailable": ["Long pauses require word-level audio timestamps, which are not retained in this MVP.", "Words per minute and filler counts require voice duration and complete speech transcription; metrics may be unavailable or incomplete."]},
        "transcript": answers, "transcription_note": "Speech recognition can omit words, mishear accents, or alter names and technical terms. Check the transcript; uncertain recognition should not be treated as candidate error.",
    }
    session["report"] = report
    session["status"] = "completed"
    session["stage"] = "closing"
    save_interview(session)
    return report


def end_interview(session):
    """Finish the session and generate its evidence-linked report."""
    return build_report(session)


def get_session(session_id):
    """Load a saved interview session by its ID."""
    return load_interview(session_id)
