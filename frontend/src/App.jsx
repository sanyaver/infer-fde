import { useState, useEffect, useRef, useCallback } from "react";

// In dev: Vite proxies /api → http://localhost:8000
// In prod (docker): nginx proxies /api → http://backend:8000
const API = "/api";

const STATUS_LABELS = {
  pending: "Starting...",
  logging_in: "Logging in to carrier portal...",
  mfa_required: "Carrier sent you a code — enter it below",
  mfa_submitted: "Submitting code...",
  mfa_processing: "Verifying code...",
  fetching_docs: "Fetching your documents...",
  completed: "Done",
  error: "Error",
};

const STYLES = {
  root: {
    fontFamily: "monospace",
    maxWidth: 800,
    margin: "40px auto",
    padding: "0 20px",
    color: "#111",
  },
  h1: { fontSize: 22, marginBottom: 4 },
  sub: { color: "#555", marginBottom: 32, fontSize: 13 },
  label: { display: "block", fontWeight: "bold", marginBottom: 4 },
  input: {
    display: "block",
    width: "100%",
    padding: "8px 10px",
    fontSize: 14,
    border: "1px solid #999",
    borderRadius: 3,
    marginBottom: 16,
    boxSizing: "border-box",
  },
  select: {
    display: "block",
    width: "100%",
    padding: "8px 10px",
    fontSize: 14,
    border: "1px solid #999",
    borderRadius: 3,
    marginBottom: 16,
    boxSizing: "border-box",
    background: "#fff",
  },
  btn: {
    padding: "9px 22px",
    fontSize: 14,
    background: "#1a1a1a",
    color: "#fff",
    border: "none",
    borderRadius: 3,
    cursor: "pointer",
  },
  btnDisabled: {
    padding: "9px 22px",
    fontSize: 14,
    background: "#888",
    color: "#fff",
    border: "none",
    borderRadius: 3,
    cursor: "not-allowed",
  },
  status: {
    margin: "20px 0",
    padding: "12px 16px",
    background: "#f5f5f5",
    border: "1px solid #ddd",
    borderRadius: 3,
    fontSize: 13,
  },
  error: {
    margin: "20px 0",
    padding: "12px 16px",
    background: "#fff0f0",
    border: "1px solid #f88",
    borderRadius: 3,
    fontSize: 13,
    color: "#c00",
  },
  mfaBox: {
    margin: "20px 0",
    padding: "16px",
    background: "#fffbee",
    border: "1px solid #f0c040",
    borderRadius: 3,
  },
  docList: { listStyle: "none", padding: 0, margin: "16px 0" },
  docItem: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 14px",
    border: "1px solid #ddd",
    borderRadius: 3,
    marginBottom: 8,
    background: "#fff",
  },
  docName: { fontSize: 13, fontWeight: "bold" },
  docType: { fontSize: 11, color: "#666", marginTop: 2 },
  viewBtn: {
    padding: "5px 12px",
    fontSize: 12,
    background: "#006400",
    color: "#fff",
    border: "none",
    borderRadius: 3,
    cursor: "pointer",
    textDecoration: "none",
  },
  pdfViewer: {
    width: "100%",
    height: 600,
    border: "1px solid #ccc",
    marginTop: 16,
  },
  spinner: { display: "inline-block", marginRight: 8 },
};

function Spinner() {
  const [dots, setDots] = useState(".");
  useEffect(() => {
    const t = setInterval(() => setDots((d) => (d.length >= 3 ? "." : d + ".")), 400);
    return () => clearInterval(t);
  }, []);
  return <span style={STYLES.spinner}>{dots}</span>;
}

export default function App() {
  const [carriers, setCarriers] = useState([]);
  const [carrier, setCarrier] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [sessionStatus, setSessionStatus] = useState(null); // full status object
  const [mfaCode, setMfaCode] = useState("");
  const [mfaSubmitting, setMfaSubmitting] = useState(false);
  const [activePdf, setActivePdf] = useState(null); // { url, name }
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef(null);

  // Load carrier list on mount
  useEffect(() => {
    fetch(`${API}/carriers`)
      .then((r) => r.json())
      .then((data) => {
        setCarriers(data);
        if (data.length > 0) setCarrier(data[0].id);
      })
      .catch(() => {
        const fallback = [
          { id: "geico", label: "Geico" },
          { id: "allstate", label: "Allstate" },
          { id: "progressive", label: "Progressive" },
          { id: "liberty_mutual", label: "Liberty Mutual" },
        ];
        setCarriers(fallback);
        setCarrier(fallback[0].id);
      });
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (sid) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`${API}/session-status/${sid}`);
          if (!res.ok) return;
          const data = await res.json();
          setSessionStatus(data);
          if (data.status === "completed" || data.status === "error") {
            stopPolling();
          }
        } catch (_) {}
      }, 1000);
    },
    [stopPolling]
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  async function handleStart(e) {
    e.preventDefault();
    if (!carrier || !username || !password) return;
    setSubmitting(true);
    setSessionStatus(null);
    setActivePdf(null);
    setMfaCode("");
    stopPolling();

    try {
      const res = await fetch(`${API}/start-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ carrier, username, password }),
      });
      const text = await res.text();
      if (!text) throw new Error(`Empty response from server (status ${res.status}) — check backend`);
      const data = JSON.parse(text);
      if (!res.ok) throw new Error(data.detail || "Failed to start session");
      setSessionId(data.session_id);
      startPolling(data.session_id);
    } catch (err) {
      setSessionStatus({ status: "error", error: err.message });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleMFA(e) {
    e.preventDefault();
    if (!mfaCode.trim() || !sessionId) return;
    setMfaSubmitting(true);
    try {
      const res = await fetch(`${API}/submit-mfa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, code: mfaCode.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "MFA submission failed");
      // Resume polling — it will pick up status changes
      startPolling(sessionId);
    } catch (err) {
      setSessionStatus((prev) => ({
        ...(prev || {}),
        status: "error",
        error: err.message,
      }));
    } finally {
      setMfaSubmitting(false);
    }
  }

  function handleReset() {
    stopPolling();
    setSessionId(null);
    setSessionStatus(null);
    setMfaCode("");
    setActivePdf(null);
    setUsername("");
    setPassword("");
  }

  const status = sessionStatus?.status;
  const isRunning =
    status && !["completed", "error", "mfa_required"].includes(status);

  return (
    <div style={STYLES.root}>
      <h1 style={STYLES.h1}>Insurance Document Puller</h1>
      <p style={STYLES.sub}>
        Pull policy documents directly from your carrier portal.
      </p>

      {/* ── Login Form ── */}
      {!sessionId || status === "error" ? (
        <form onSubmit={handleStart}>
          <label style={STYLES.label}>Carrier</label>
          <select
            style={STYLES.select}
            value={carrier}
            onChange={(e) => setCarrier(e.target.value)}
            disabled={submitting}
          >
            {carriers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>

          <label style={STYLES.label}>Username / Email</label>
          <input
            style={STYLES.input}
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="portal username or email"
            disabled={submitting}
            required
          />

          <label style={STYLES.label}>Password</label>
          <input
            style={STYLES.input}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="portal password"
            disabled={submitting}
            required
          />

          {status === "error" && (
            <div style={STYLES.error}>
              {sessionStatus?.error || "Unknown error"}
              <div style={{ marginTop: 8 }}>
                <button type="button" style={STYLES.btn} onClick={handleReset}>
                  Try again
                </button>
              </div>
            </div>
          )}

          <button
            type="submit"
            style={submitting ? STYLES.btnDisabled : STYLES.btn}
            disabled={submitting}
          >
            {submitting ? "Starting..." : "Pull Documents"}
          </button>
        </form>
      ) : null}

      {/* ── Status Banner ── */}
      {status && status !== "error" && status !== "completed" && (
        <div style={STYLES.status}>
          <Spinner />
          {STATUS_LABELS[status] || status}
        </div>
      )}

      {/* ── MFA Prompt ── */}
      {status === "mfa_required" && (
        <div style={STYLES.mfaBox}>
          <p style={{ margin: "0 0 12px", fontWeight: "bold" }}>
            Two-factor authentication required
          </p>
          <p style={{ margin: "0 0 12px", fontSize: 13 }}>
            The carrier sent a code to your phone or email. Enter it below.
          </p>
          <form onSubmit={handleMFA} style={{ display: "flex", gap: 8 }}>
            <input
              style={{ ...STYLES.input, marginBottom: 0, maxWidth: 180 }}
              type="text"
              autoComplete="one-time-code"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              placeholder="verification code"
              maxLength={12}
              autoFocus
              required
            />
            <button
              type="submit"
              style={mfaSubmitting ? STYLES.btnDisabled : STYLES.btn}
              disabled={mfaSubmitting}
            >
              {mfaSubmitting ? "Verifying..." : "Submit"}
            </button>
          </form>
        </div>
      )}

      {/* ── Documents ── */}
      {status === "completed" && (
        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h2 style={{ fontSize: 16, margin: "24px 0 8px" }}>
              Your Documents ({sessionStatus?.documents?.length ?? 0})
            </h2>
            <button
              style={{ ...STYLES.btn, fontSize: 12, padding: "5px 12px" }}
              onClick={handleReset}
            >
              New Search
            </button>
          </div>

          {sessionStatus?.documents?.length === 0 ? (
            <p style={{ color: "#666", fontSize: 13 }}>No documents found.</p>
          ) : (
            <ul style={STYLES.docList}>
              {sessionStatus.documents.map((doc) => (
                <li key={doc.id} style={STYLES.docItem}>
                  <div>
                    <div style={STYLES.docName}>{doc.name}</div>
                    <div style={STYLES.docType}>{doc.type}</div>
                    {doc.error && (
                      <div style={{ color: "#c00", fontSize: 11, marginTop: 2 }}>
                        {doc.error}
                      </div>
                    )}
                  </div>
                  {doc.available !== false && (
                    <button
                      style={STYLES.viewBtn}
                      onClick={() =>
                        setActivePdf({
                          url: `${API}/document/${sessionId}/${doc.id}`,
                          name: doc.name,
                        })
                      }
                    >
                      View PDF
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* ── PDF Viewer ── */}
          {activePdf && (
            <div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginTop: 16,
                }}
              >
                <strong style={{ fontSize: 13 }}>{activePdf.name}</strong>
                <div style={{ display: "flex", gap: 8 }}>
                  <a
                    href={activePdf.url}
                    download
                    style={{ ...STYLES.viewBtn, textDecoration: "none" }}
                  >
                    Download
                  </a>
                  <button
                    style={{ ...STYLES.btn, fontSize: 12, padding: "5px 12px" }}
                    onClick={() => setActivePdf(null)}
                  >
                    Close
                  </button>
                </div>
              </div>
              <iframe
                key={activePdf.url}
                src={activePdf.url}
                style={STYLES.pdfViewer}
                title={activePdf.name}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
