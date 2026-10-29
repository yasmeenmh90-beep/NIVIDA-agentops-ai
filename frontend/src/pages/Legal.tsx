import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { PublicFooter, PublicHeader } from "./Landing";
import { usePageMeta } from "../seo";

function Frame({ title, description, index, children }: { title: string; description: string; index: boolean; children: ReactNode }) {
  usePageMeta(`${title} · AgentOps AI`, description, index);
  return (
    <div className="public-site">
      <PublicHeader />
      <main id="main-content" className="doc">
        <p className="crumb"><Link to="/">Home</Link> / {title}</p>
        <h1>{title}</h1>
        {children}
      </main>
      <PublicFooter />
    </div>
  );
}

export function PrivacyPage({ framed = true }: { framed?: boolean }) {
  const body = (
    <>
      <p className="lede">This notice describes the AgentOps AI dashboard as implemented in this repository. It is not a certification of legal compliance, and it does not cover a backend you configure with extra services.</p>
      <h2>Who operates it</h2>
      <p>AgentOps AI is a hackathon project. Whoever runs the backend and the dashboard decides where data is stored. This page does not name a company, address, or data-protection officer because the software does not set one.</p>
      <h2>Data kept in the browser</h2>
      <ul>
        <li>A session token after you sign in, including the demo viewer account. It is stored in local storage, not in a cookie. The password is not stored.</li>
        <li>The backend URL, an optional AgentOps service API key, your approver name, and desktop-alert preferences, also in local storage.</li>
        <li>Signing out also removes a leftover startup flag (<code>agentops.booted</code>) if an older session stored one. The current dashboard does not set that flag.</li>
      </ul>
      <p>The live incident stream sends that credential as an <code>api_key</code> query parameter because browser event streams cannot set an <code>Authorization</code> header. Use the dashboard on a network you trust, and sign out on a shared computer.</p>
      <h2>Data the backend processes</h2>
      <p>When you connect a repository and send errors, the backend you configured can store incident titles, tracebacks, source paths, proposed diffs, test logs, audit events, account email and name, and a GitHub pull-request URL. Retention is the database that backend uses. This dashboard does not offer a separate retention period.</p>
      <h2>External services</h2>
      <p>The backend calls a service only when the operator puts that service's credentials in server-side environment variables. Depending on configuration, those services can include Nebius Token Factory for the model, Tavily for web search, GitHub for repositories and pull requests, Sentry for incoming webhooks, and Slack, email, or a webhook for notifications. The dashboard does not receive those secrets and does not put them in the page.</p>
      <h2>Analytics</h2>
      <p>No analytics are sent unless the dashboard is built with <code>VITE_ANALYTICS_ENDPOINT</code>. If that address is set, the page asks before sending anything. An allowed event contains the route template, such as <code>/incidents/:id</code>. It does not include source code, logs, stack traces, diffs, or access tokens. Declining stores that choice in local storage and sends nothing.</p>
      <h2>Cookies</h2>
      <p>The dashboard does not set cookies for tracking, and it does not show a cookie banner when analytics are off. Sign-in uses local storage. If a hosting provider adds its own cookies, that provider's notice applies.</p>
      <h2>Controls</h2>
      <ul>
        <li>Sign out to delete the session token from this browser and revoke it on the backend.</li>
        <li>Clear site data for this origin to remove the saved backend URL and API key.</li>
        <li>Ask the operator of the backend to remove an account or an incident. The dashboard does not claim a self-serve deletion API beyond the actions the backend already exposes.</li>
      </ul>
      <p>Questions about a deployment go to the person who operates that deployment. This repository does not publish a support address.</p>
    </>
  );
  if (!framed) return <article className="doc doc-body"><h1>Privacy policy</h1>{body}</article>;
  return <Frame title="Privacy policy" description="What the AgentOps AI dashboard stores in the browser and which services the backend can call." index>{body}</Frame>;
}

export function TermsPage({ framed = true }: { framed?: boolean }) {
  const body = (
    <>
      <p className="lede">These terms describe permitted use of this copy of AgentOps AI. They are not a substitute for the terms of GitHub, NVIDIA, Nebius, or any other service you connect.</p>
      <h2>What the software does</h2>
      <p>AgentOps AI detects Python incidents, investigates a repository you register, proposes a patch, runs tests, and can open a GitHub pull request after a person approves. It does not merge that pull request and it does not deploy to production by itself.</p>
      <h2>Your responsibilities</h2>
      <ul>
        <li>Connect only repositories and credentials you are allowed to use.</li>
        <li>Review the diagnosis, the diff, and the test evidence before approving.</li>
        <li>Treat model output as a proposal. A passing test is evidence about that suite, not a guarantee that the change is correct or safe.</li>
        <li>Keep API keys and the GitHub token on the server. Do not paste them into incident notes or tracebacks.</li>
      </ul>
      <h2>Accounts</h2>
      <p>Demo access signs in as a viewer account that the backend creates. It can read the workspace. The first other account created on a backend is the admin. Later accounts are viewers until an admin changes their role. The demo account cannot be promoted. Viewers can read the workspace. Members and admins can approve, reject, and send errors, subject to the backend's checks.</p>
      <h2>Acceptable use</h2>
      <p>Do not use the service to attack systems you do not operate, to submit another person's secrets, or to bypass the approval gate. The sandbox and the human approval step are part of the product. Disabling them to move faster is outside the intended use of this software.</p>
      <h2>No warranty</h2>
      <p>The software is provided as a hackathon project under the repository license. It can misdiagnose an incident, propose a wrong patch, or fail to reach the backend. You are responsible for reviewing changes before they land on a branch you care about.</p>
    </>
  );
  if (!framed) return <article className="doc doc-body"><h1>Terms</h1>{body}</article>;
  return <Frame title="Terms" description="Permitted use of AgentOps AI, human approval, and the limits of AI-generated code changes." index>{body}</Frame>;
}

export function HelpPage({ framed = true }: { framed?: boolean }) {
  const body = (
    <>
      <p className="lede">Use this when you need to know what the workspace is asking you to decide.</p>
      <h2>Start</h2>
      <ol>
        <li>Run the backend and open the dashboard.</li>
        <li>Use demo access on the sign-in page, or create the admin account and sign in.</li>
        <li>Add a Python repository that already has a pytest suite, and wait until its setup status is ready.</li>
        <li>After you <Link to={framed ? "/login" : "/send"}>{framed ? "sign in" : "send a test error"}</Link>, submit a traceback for a ready repository, or connect Sentry if that webhook is configured.</li>
      </ol>
      <h2>Read an incident</h2>
      <p>The incident page is the review surface. The overview shows severity, status, and where it crashed. The timeline shows detector, investigator, fixer, tester, and approval. The diagnosis separates the explanation from the files it cites. The diff is a proposal until a pull request exists. Test results say whether the patch was verified. Approve only when that evidence is enough for you.</p>
      <h2>Approval</h2>
      <p>Approve and create PR opens a pull request on a new branch. Reject asks for a reason. If tests did not pass, the incident is not left waiting for approval. Ask for changes on an open pull request when a reviewer wants a revision. Stop the agents when a run should halt at the next checkpoint.</p>
      <h2>If the page cannot load data</h2>
      <p>Open Settings and check the backend URL. The origin of this dashboard must be listed in the backend's <code>CORS_ORIGINS</code>. A service API key is optional and is stored only in this browser.</p>
    </>
  );
  if (!framed) return <article className="doc doc-body"><h1>Help</h1>{body}</article>;
  return <Frame title="Help" description="How to connect a repository, read an incident, and approve a verified fix in AgentOps AI." index>{body}</Frame>;
}
