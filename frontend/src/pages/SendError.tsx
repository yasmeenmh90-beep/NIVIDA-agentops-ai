import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Project, api, useApi } from "../api";
import { ErrorBox } from "../components/ui";
import { useMe } from "../auth";

const EXAMPLE = `Traceback (most recent call last):
  File "/app/orders/main.py", line 40, in create_order
    total = order_total(items, body.discount_code)
  File "/app/orders/pricing.py", line 19, in order_total
    return round(subtotal * (1 - rate), 2)
TypeError: unsupported operand type(s) for -: 'int' and 'NoneType'`;

interface IngestResult { project: string; events: number; new: number[]; updated: number[] }

export default function SendErrorPage() {
  const { canAct } = useMe();
  const { data: projects } = useApi<Project[]>("/projects");
  const ready = (projects ?? []).filter((p) => p.setup_status === "ready");
  const [project, setProject] = useState("");
  const [method, setMethod] = useState("POST");
  const [path, setPath] = useState("/orders");
  const [source, setSource] = useState("dashboard");
  const [tb, setTb] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);

  useEffect(() => { if (!project && ready.length) setProject(ready[0].name); }, [ready.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try {
      let r: IngestResult;
      if (file) {
        const form = new FormData();
        form.append("project", project);
        form.append("file", file);
        r = await api<IngestResult>("/ingest/file", { form, method: "POST" });
      } else {
        r = await api<IngestResult>("/ingest/events", {
          body: { project, source, events: [{ traceback: tb.trim() + "\n", method: path ? method : "", path, service: source }] },
        });
      }
      setResult(r);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Send a test error</h1>
          <p>Paste a real Python traceback from your app, or upload a log file. This goes through the same pipeline as production errors.</p>
        </div>
      </div>

      <form className="panel panel-pad stack" onSubmit={submit} style={{ maxWidth: 900 }}>
        {!canAct && <div className="alert info">View-only access: you can't send errors. Ask an admin to make you a member.</div>}
        {projects && ready.length === 0 && (
          <div className="alert info">No repository is ready yet. <Link to="/projects">Add one first</Link>.</div>
        )}
        <div className="form-grid">
          <div className="field">
            <label htmlFor="s-proj">Repository</label>
            <select id="s-proj" className="select" value={project} onChange={(e) => setProject(e.target.value)} required>
              {ready.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="s-src">Source</label>
            <input id="s-src" className="input" value={source} onChange={(e) => setSource(e.target.value)} />
            <span className="hint">Which service or server reported it.</span>
          </div>
          <div className="field">
            <label htmlFor="s-method">HTTP method</label>
            <select id="s-method" className="select" value={method} onChange={(e) => setMethod(e.target.value)}>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}
            </select>
            <span className="hint">Errors on write requests are rated higher severity.</span>
          </div>
          <div className="field">
            <label htmlFor="s-path">Route (optional)</label>
            <input id="s-path" className="input mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/orders" />
          </div>
          <div className="field full">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label htmlFor="s-tb">Traceback</label>
              <button type="button" className="btn sm ghost" onClick={() => setTb(EXAMPLE)}>Fill in an example</button>
            </div>
            <textarea id="s-tb" className="textarea" value={tb} onChange={(e) => setTb(e.target.value)} disabled={!!file} placeholder="Traceback (most recent call last):&#10;  File ..." />
            <span className="hint">File paths are matched to your repository by their ending, so container paths like /app/... work.</span>
          </div>
          <div className="field full">
            <label htmlFor="s-file">Or upload a log file</label>
            <input id="s-file" type="file" className="input" accept=".log,.txt,.json,.jsonl" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
        </div>
        <ErrorBox message={error} />
        {result && (
          <div className="alert ok">
            {result.events === 0
              ? "No Python traceback was found in that input."
              : <>
                  Found {result.events} error{result.events > 1 ? "s" : ""}.{" "}
                  {result.new.map((id) => <span key={id}>Opened <Link to={`/incidents/${id}`}>incident #{id}</Link>. </span>)}
                  {result.updated.map((id) => <span key={id}>Grouped into <Link to={`/incidents/${id}`}>incident #{id}</Link>. </span>)}
                </>}
          </div>
        )}
        <div>
          <button className="btn primary" disabled={!canAct || busy || !project || (!tb.trim() && !file)}>{busy ? "Sending" : "Send error"}</button>
        </div>
      </form>
    </>
  );
}
