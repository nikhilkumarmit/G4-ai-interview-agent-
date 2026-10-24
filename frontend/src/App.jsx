/* eslint-disable react/prop-types */
import { useCallback, useEffect, useState } from 'react'
import { LiveKitRoom, RoomAudioRenderer, useLocalParticipant, useTrackTranscription, useVoiceAssistant } from '@livekit/components-react'
import '@livekit/components-styles'
import { Track } from 'livekit-client'
import './App.css'

const API = '/api'
const stages = ['intro', 'resume', 'behavioral', 'role_specific', 'candidate_questions', 'closing']

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.')
  return data
}

function VoicePanel({ onState }) {
  const { state, agentTranscriptions, audioTrack } = useVoiceAssistant()
  const localParticipant = useLocalParticipant()
  const { segments: userTranscriptions } = useTrackTranscription({
    publication: localParticipant.microphoneTrack,
    source: Track.Source.Microphone,
    participant: localParticipant.localParticipant,
  })
  const isSpeaking = state === 'speaking'
  useEffect(() => {
    if (['listening', 'speaking', 'thinking'].includes(state)) onState?.('connected')
  }, [state, onState])
  const lines = [
    ...(agentTranscriptions || []).map((line) => ({ ...line, speaker: 'Interviewer' })),
    ...(userTranscriptions || []).map((line) => ({ ...line, speaker: 'You' })),
  ].sort((a, b) => a.firstReceivedTime - b.firstReceivedTime).slice(-12)
  return <div className="voice-panel">
    <div className={`voice-orb ${isSpeaking ? 'speaking' : ''}`} aria-label={isSpeaking ? 'Interviewer speaking' : 'Microphone connected'}><span /><span /><span /><span /><span /></div>
    <div><b>{state === 'speaking' ? 'Interviewer speaking' : state === 'listening' ? 'Listening' : 'Voice connected'}</b><small>Mic active · interrupt any time</small></div>
    <div className="voice-transcript" aria-live="polite">{lines.map((line, i) => <p key={line.id || i}><b>{line.speaker}: </b>{line.text}</p>)}</div>
    {audioTrack && <span className="sr-only">Audio response available</span>}
  </div>
}

function App() {
  const [resumeFile, setResumeFile] = useState(null)
  const [jdFile, setJdFile] = useState(null)
  const [resumeText, setResumeText] = useState('')
  const [jdText, setJdText] = useState('')
  const [session, setSession] = useState(null)
  const [token, setToken] = useState(null)
  const [serverUrl, setServerUrl] = useState(import.meta.env.VITE_LIVEKIT_URL || '')
  const [consent, setConsent] = useState(false)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [voiceStatus, setVoiceStatus] = useState('disconnected')
  const [notice, setNotice] = useState('')
  const [now, setNow] = useState(Date.now())
  const [agentReady, setAgentReady] = useState(false)

  const mergeSession = useCallback((next) => setSession((current) => ({ ...current, ...next })), [])
  const recover = async () => {
    setBusy(true); setError('')
    try {
      const found = await request('/interviews/recent')
      if (found.status === 'completed') { setSession(found); setReport(found.report) }
      else { setSession(found); setNotice('Recovered the latest saved interview. Re-consent to resume voice processing.') }
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  useEffect(() => {
    request('/health').then((health) => {
      if (!health.groq_configured) setNotice('Groq is not configured on the backend. Text practice remains available; voice requires GROQ_API_KEY.')
    }).catch(() => setNotice('Backend is not reachable. Start the backend to upload documents and create an interview.'))
  }, [])

  useEffect(() => {
    if (session?.status !== 'active') return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [session?.status])

  useEffect(() => {
    if (!token || agentReady) return undefined
    const timeout = window.setTimeout(() => {
      setVoiceStatus('failed')
      setError('The AI interviewer did not join the LiveKit room. Check the worker and model credentials, then use text answers or retry.')
    }, 20000)
    return () => window.clearTimeout(timeout)
  }, [token, agentReady])

  const prepare = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setReport(null); setToken(null)
    try {
      const form = new FormData()
      if (resumeFile) form.append('resume', resumeFile)
      if (jdFile) form.append('job_description', jdFile)
      form.append('resume_text', resumeText)
      form.append('job_description_text', jdText)
      const created = await request('/interviews', { method: 'POST', body: form })
      setSession(created); setConsent(false)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const begin = async () => {
    if (!session || !consent) return
    setBusy(true); setError('')
    try {
      const started = await request(`/interviews/${session.id}/consent`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ consent: true }) })
      mergeSession(started)
      const connection = await request(`/getToken?interview_id=${encodeURIComponent(session.id)}`)
      setToken(connection.token)
      setServerUrl(connection.url || serverUrl)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const submitAnswer = async (event) => {
    event.preventDefault(); if (!answer.trim() || !session) return
    setBusy(true); setError('')
    try {
      const data = await request(`/interviews/${session.id}/answer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer, source: 'text' }) })
      mergeSession(data.session); setAnswer('')
      if (data.result.refused) setNotice(data.result.message)
      else if (data.result.followup) setNotice(data.result.followup)
      else setNotice('Answer saved. Continue with the next question.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const finish = async () => {
    if (!session) return
    setBusy(true); setError('')
    try {
      const data = await request(`/interviews/${session.id}/end`, { method: 'POST' })
      setReport(data.report); mergeSession(data.session); setToken(null); setVoiceStatus('disconnected')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const reset = () => { setSession(null); setToken(null); setReport(null); setAnswer(''); setConsent(false); setError(''); setNotice('') }
  const progress = session ? Math.min(session.asked_questions?.length || 0, session.question_budget || 8) : 0
  const elapsed = session?.started_epoch ? Math.floor(now / 1000 - session.started_epoch) : (session?.elapsed_seconds || 0)
  const remainingMinutes = Math.max(0, Math.ceil(((session?.time_limit_seconds || 1800) - elapsed) / 60))
  const currentStage = session?.stage || 'intro'

  return <div className="app-shell">
    <header className="topbar"><a href="#top" className="brand"><span className="brand-mark">M</span><span>m<span className="brand-accent">ockmate</span></span></a><div className="topbar-right"><span className="secure-label"><i /> Private practice space</span><button className="text-button" onClick={recover} disabled={busy}>Recover session</button></div></header>
    <main id="top" className="main-wrap">
      <section className="intro"><div className="eyebrow"><span className="eyebrow-line" /> PLACEMENT PREP, WITH PURPOSE</div><h1>Show them what<br /><em>you’re made of.</em></h1><p>Practice with an AI coach that knows your experience and the role you’re after.</p></section>
      {error && <div className="alert error" role="alert">{error}</div>}
      {notice && <div className="alert notice" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss">×</button></div>}

      {!session && <form className="documents-grid" onSubmit={prepare}>
        <DocumentCard number="01" title="Your resume" caption="We’ll find the real details worth talking about." file={resumeFile} setFile={setResumeFile} text={resumeText} setText={setResumeText} accept=".pdf,.txt"/>
        <DocumentCard number="02" title="The role" caption="A job description makes your practice more relevant." file={jdFile} setFile={setJdFile} text={jdText} setText={setJdText} accept=".pdf,.txt"/>
        <div className="prepare-row"><div><span className="tiny-label">READY WHEN YOU ARE</span><p>PDF or TXT · 8 MB maximum · Your files stay on this server</p></div><button className="primary" type="submit" disabled={busy}>{busy ? 'Preparing…' : 'Prepare my interview'} <span>↗</span></button></div>
      </form>}

      {session && !report && <>
        <section className="session-layout">
          <aside className="session-sidebar"><div className="card-heading"><span className="tiny-label">YOUR PRACTICE SESSION</span><span className="live-pill"><i />{session.status === 'active' ? 'LIVE' : session.status?.toUpperCase()}</span></div>
            <h2>{session.summary?.target_role || 'Placement interview'}</h2><p className="candidate-name">{session.summary?.candidate_name}</p>
            <div className="divider"/><span className="tiny-label">INTERVIEW FLOW</span><ol className="stage-list">{stages.slice(1).map((stage) => <li key={stage} className={stage === currentStage ? 'current' : stages.indexOf(stage) < stages.indexOf(currentStage) ? 'done' : ''}><span className="stage-dot">{stages.indexOf(stage) < stages.indexOf(currentStage) ? '✓' : String(stages.indexOf(stage)).padStart(2, '0')}</span>{stage.replace('_', ' ')}</li>)}</ol>
            <div className="progress-block"><div className="progress-label"><span>Question progress</span><b>{progress} / {session.question_budget || 8}</b></div><div className="progress-track"><i style={{ width: `${Math.min(100, progress / (session.question_budget || 8) * 100)}%` }}/></div></div>
            <div className="doc-summary"><span className="tiny-label">DOCUMENT SNAPSHOT</span><p><b>{session.summary?.skills?.length || 0}</b> skills identified</p><p><b>{session.summary?.projects?.length || 0}</b> project details found</p><p><b>{session.summary?.experience?.length || 0}</b> experience details found</p>{session.summary?.skills?.length > 0 && <div className="skill-tags">{session.summary.skills.slice(0, 5).map((skill) => <span key={skill}>{skill}</span>)}</div>}</div>
          </aside>
          <section className="interview-main"><div className="interview-topline"><div><span className="tiny-label">{currentStage.replace('_', ' ').toUpperCase()} ROUND</span><p>Take a breath. There’s no rush.</p></div><span className="timer-chip">◷ {remainingMinutes} min</span></div>
            <div className="question-card"><span className="question-label">YOUR QUESTION</span><h2>{session.current_question?.text || 'Get ready to begin.'}</h2><span className="evidence-label">Grounded in your uploaded details</span></div>
            {(session.status === 'prepared' || (session.status === 'active' && !token)) ? <div className="consent-card"><h3>{session.status === 'prepared' ? 'Before we begin' : 'Resume your interview'}</h3><p>I’m an AI interviewer. LiveKit will carry your microphone audio to the agent and Groq will process it for speech recognition. The transcript, answers, and scores are saved in the local interview database. This app does not store audio. Provider processing follows your LiveKit and Groq account settings.</p><label className="consent-check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}/><span>I consent to this audio and transcript processing.</span></label><button className="primary" disabled={!consent || busy} onClick={begin}>{busy ? 'Connecting…' : session.status === 'prepared' ? 'Start interview' : 'Resume interview'} <span>↗</span></button></div> : <>
              <div className="voice-card"><div className="voice-status-row"><span className={`status-dot ${agentReady ? 'on' : ''}`} />{agentReady ? 'AI interviewer ready · microphone connected' : voiceStatus === 'connecting' || voiceStatus === 'waiting' ? 'Waiting for AI interviewer…' : 'Voice unavailable — use text answer below'}<span className="latency-label">Speech is processed by Groq</span></div>
                {token ? <LiveKitRoom serverUrl={serverUrl} token={token} connect audio video={false} onConnected={() => setVoiceStatus('waiting')} onDisconnected={() => { setVoiceStatus('disconnected'); setAgentReady(false) }} onError={(e) => { setVoiceStatus('failed'); setError(`Voice connection failed: ${e?.message || 'Check LiveKit settings and worker.'}`) }}><RoomAudioRenderer/><VoicePanel onState={(state) => { if (state === 'connected') { setAgentReady(true); setVoiceStatus('connected') } }}/></LiveKitRoom> : <p className="voice-hint">You can type your answer if voice connection is not available. Voice only starts after consent.</p>}
              </div>
              {!agentReady && <form className="answer-form" onSubmit={submitAnswer}><label htmlFor="answer">Or type your answer</label><textarea id="answer" rows="3" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Use a specific example. What did you do, and what happened?"/><div className="answer-actions"><span>Saved after each answer</span><button className="secondary" disabled={busy || !answer.trim()}>{busy ? 'Saving…' : 'Save answer'} ↗</button></div></form>}
            </>}
            <div className="bottom-actions"><button className="end-button" onClick={finish} disabled={busy}>End interview <span>■</span></button><span>Your progress saves automatically</span></div>
          </section>
        </section>
      </>}

      {report && <Report report={report} onRestart={reset}/>}
      <footer><span>MOCKMATE <span className="footer-dot">·</span> PRACTICE MAKES PROGRESS</span><span>Feedback for practice only — never a hiring decision.</span></footer>
    </main>
  </div>
}

function DocumentCard({ number, title, caption, file, setFile, text, setText, accept }) {
  return <div className="document-card"><div className="document-head"><span className="document-number">{number}</span><div><h2>{title}</h2><p>{caption}</p></div></div><label className="dropzone"><input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] || null)}/><span className="upload-icon">↑</span><b>{file ? file.name : 'Drop your file here or browse'}</b><small>PDF or TXT · up to 8 MB</small></label><div className="or-divider"><span>OR PASTE TEXT</span></div><textarea value={text} onChange={(e) => setText(e.target.value)} rows="4" placeholder={title === 'Your resume' ? 'Paste resume text here…' : 'Paste the job description here…'}/></div>
}

function Report({ report, onRestart }) {
  return <section className="report-wrap"><div className="report-header"><div><span className="tiny-label">SESSION COMPLETE</span><h2>A little wiser for next time.</h2><p>Every score below links to what was said in your practice session.</p></div><div className="overall-score"><b>{report.overall ?? '—'}</b><span>out of 5</span></div></div>
    <div className="report-grid"><div className="report-panel"><span className="tiny-label">ANSWER BY ANSWER</span>{(report.scores || []).map((item, i) => <article className="score-row" key={i}><div className="score-title"><b>{item.question}</b><span>{item.average}/5</span></div><blockquote>“{item.evidence}”</blockquote><div className="score-dimensions">{Object.entries(item.scores).map(([key, value]) => <span key={key}>{key} <b>{value}/5</b></span>)}</div></article>)}{!report.scores?.length && <p>No answer evidence was recorded in this session.</p>}</div><div className="report-side"><div className="report-panel"><span className="tiny-label">WHAT WENT WELL</span><ul>{(report.strengths || []).map((s) => <li key={s}>{s}</li>)}</ul></div><div className="report-panel"><span className="tiny-label">NEXT TIME, TRY</span><ul>{(report.improvements || []).map((s) => <li key={s}>{s}</li>)}</ul></div><div className="report-panel metrics"><span className="tiny-label">SPEECH METRICS</span><p>Words per minute <b>{report.metrics?.words_per_minute ?? 'Unavailable'}</b></p><p>Long pauses <b>{report.metrics?.long_pauses ?? 'Unavailable'}</b></p><p>Filler words <b>{report.metrics?.filler_word_count ?? 'Unavailable'}</b></p><small>{report.transcription_note}</small></div></div></div><div className="report-actions"><button className="primary" onClick={onRestart}>Start another practice round ↗</button></div>
  </section>
}

export default App
