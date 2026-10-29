import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BrandMark } from "../components/ui";
import { usePageMeta } from "../seo";

const HeroScene = lazy(() => import("../components/HeroScene"));

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch { return false; }
}

function HeroFallback() {
  return (
    <div className="hero-fallback" aria-hidden="true">
      <span className="orbit orbit-a" />
      <span className="orbit orbit-b" />
      <span className="core" />
    </div>
  );
}

const STAGES = [
  ["Detect", "Group a Python traceback by exception, file, and function, and set a severity."],
  ["Investigate", "Read the registered repository and name a cause with file and line evidence."],
  ["Fix", "Propose a small Python patch and a regression test. The model does not get a shell."],
  ["Test", "Run the repository's pytest suite in a sandbox, before and after the patch."],
  ["Approve", "A person reviews the diagnosis, diff, and test evidence, then opens a pull request."],
];

export function PublicHeader() {
  return (
    <header className="public-bar">
      <Link to="/" className="brand">
        <BrandMark />
        <span>
          <span className="brand-name">AgentOps<span className="brand-ai">.ai</span></span>
          <span className="brand-sub">INCIDENT ENGINEERING</span>
        </span>
      </Link>
      <nav className="public-nav" aria-label="Site">
        <a href="/#workflow">Workflow</a>
        <Link to="/help">Help</Link>
        <Link className="btn primary" to="/login">Open dashboard</Link>
      </nav>
    </header>
  );
}

export function PublicFooter() {
  return (
    <footer className="public-foot">
      <div>
        <strong>AgentOps AI</strong>
        <p>A hackathon project for the Nebius × NVIDIA Global AI Hackathon 2026. Not an NVIDIA or Nebius product.</p>
      </div>
      <nav aria-label="Footer">
        <Link to="/help">Help</Link>
        <Link to="/privacy">Privacy</Link>
        <Link to="/terms">Terms</Link>
        <Link to="/login">Sign in</Link>
        <a href="https://github.com/yasmeenmh90-beep/NVIDIA-agentops-ai" rel="noreferrer">Source</a>
      </nav>
    </footer>
  );
}

export default function LandingPage({ backendError }: { backendError?: string }) {
  const [scene, setScene] = useState(false);
  usePageMeta(
    "AgentOps AI · Production incidents, meet their match",
    "AgentOps AI investigates incidents, tests proposed code fixes, and prepares pull requests for your approval.",
    true,
  );
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setScene(!reduce && webglAvailable());
    const nodes = document.querySelectorAll(".reveal");
    if (reduce) { nodes.forEach((n) => n.classList.add("in")); return; }
    const seen = new IntersectionObserver((entries) => {
      entries.forEach((entry) => { if (entry.isIntersecting) entry.target.classList.add("in"); });
    }, { threshold: 0.2 });
    nodes.forEach((n) => seen.observe(n));
    return () => seen.disconnect();
  }, []);
  return (
    <div className="public-site">
      <PublicHeader />
      <main id="main-content">
        <section className="hero">
          <div className="hero-grid">
            <div className="hero-copy">
              <p className="eyebrow">DETECT · INVESTIGATE · FIX · TEST · APPROVE</p>
              <h1>Production incidents. Meet their match.</h1>
              <p className="lede">From root-cause analysis to verified code fixes, AgentOps AI investigates incidents, tests proposed changes, and prepares pull requests for your approval.</p>
              <div className="hero-actions">
                <Link className="btn primary" to="/login">Open dashboard</Link>
                <a className="btn" href="#workflow">See the workflow</a>
              </div>
              {backendError && <p className="hero-note" role="status">The configured backend is not reachable yet. Sign-in, including demo access, needs that server. Settings can point this browser at a running one.</p>}
            </div>
            <div className="hero-stage" aria-hidden="true">
              {scene ? (
                <Suspense fallback={<HeroFallback />}>
                  <HeroScene />
                </Suspense>
              ) : <HeroFallback />}
            </div>
          </div>
        </section>

        <section id="workflow" className="band reveal">
          <div className="band-head">
            <h2>Five stages. One approval.</h2>
            <p>Each stage reports its own result. A later stage does not appear successful until the backend says it finished.</p>
          </div>
          <ol className="stage-grid">
            {STAGES.map(([name, copy], i) => (
              <li key={name}>
                <span>0{i + 1}</span>
                <h3>{name}</h3>
                <p>{copy}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="band split-band reveal">
          <article>
            <h2>Tests run before anyone is asked to approve.</h2>
            <p>A fix is treated as verified only when the new regression test fails on the original code, passes after the patch, and no previously passing test starts failing. The run happens in Docker with no network, or in a subprocess with a scrubbed environment.</p>
          </article>
          <article>
            <h2>A person opens the pull request.</h2>
            <p>Approving creates a pull request on a new branch and records who approved it. Rejecting asks for a reason. AgentOps does not merge the pull request and does not deploy the change.</p>
          </article>
        </section>

        <section className="band reveal">
          <div className="band-head">
            <h2>What the workspace is built on</h2>
            <p>The dashboard is a client. Investigation, patching, and test runs stay on the backend you connect.</p>
          </div>
          <ul className="stack-list">
            <li><strong>Dashboard</strong><span>React and TypeScript. Live incident updates use the backend's event stream, with polling if the stream drops.</span></li>
            <li><strong>Orchestrator</strong><span>FastAPI. Detector, investigator, fixer, and tester run server-side.</span></li>
            <li><strong>Model</strong><span>NVIDIA Nemotron through Nebius Token Factory, when the backend has a key configured.</span></li>
            <li><strong>Verification</strong><span>The repository's own pytest suite, inside the configured sandbox.</span></li>
            <li><strong>GitHub</strong><span>Pull requests are created after approval. Review comments can ask for a revision.</span></li>
          </ul>
        </section>

        <section className="closing">
          <h2>Open the workspace when an incident needs a decision.</h2>
          <p>Demo access opens the workspace in this browser tab. Approving a fix still depends on the backend you connect.</p>
          <Link className="btn primary" to="/login">Open dashboard</Link>
        </section>
      </main>
      <PublicFooter />
    </div>
  );
}
