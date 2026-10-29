import { Link } from "react-router-dom";
import { PublicFooter, PublicHeader } from "./Landing";
import { usePageMeta } from "../seo";

export default function NotFoundPage({ framed = true }: { framed?: boolean }) {
  usePageMeta("Page not found · AgentOps AI", "That address is not part of AgentOps AI.", false);
  const body = (
    <div className="not-found">
      <p className="eyebrow">404</p>
      <h1>This page is not in the workspace.</h1>
      <p>The address does not match a dashboard screen or a public page.</p>
      <div className="hero-actions">
        <Link className="btn primary" to="/">{framed ? "Back home" : "Back to incidents"}</Link>
        {framed && <Link className="btn" to="/help">Read the help page</Link>}
      </div>
    </div>
  );
  if (!framed) return body;
  return (
    <div className="public-site">
      <PublicHeader />
      <main id="main-content" className="doc">{body}</main>
      <PublicFooter />
    </div>
  );
}
