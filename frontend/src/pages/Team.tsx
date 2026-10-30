import { useState } from "react";
import { Role, UserAccount, ago, api, useApi } from "../api";
import { useMe } from "../auth";
import { ErrorBox } from "../components/ui";

const ROLE_HELP: Record<Role, string> = {
  viewer: "Can see everything, can't change anything",
  member: "Can approve, reject, ask for changes, stop and re-run, and manage repositories",
  admin: "Member, plus manages who has access",
};

function Avatar({ u }: { u: UserAccount }) {
  return u.avatar_url
    ? <img className="avatar" src={u.avatar_url} alt="" />
    : <span className="avatar">{u.name.slice(0, 1).toUpperCase()}</span>;
}

export default function TeamPage() {
  const { me, isAdmin } = useMe();
  const { data: users, error, reload } = useApi<UserAccount[]>(isAdmin ? "/users" : null);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);

  const setRole = async (u: UserAccount, role: Role) => {
    setBusy(u.id); setErr(null);
    try { await api(`/users/${u.id}`, { method: "PATCH", body: { role } }); reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  };
  const remove = async (u: UserAccount) => {
    setBusy(u.id); setErr(null);
    try { await api(`/users/${u.id}`, { method: "DELETE" }); setConfirm(null); reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Team</h1>
          <p>Who can see AgentOps and who can approve fixes. New accounts start as viewers.</p>
        </div>
      </div>
      {!isAdmin ? (
        <div className="alert info">Only admins can manage the team. You're signed in as {me.name} ({me.role}).</div>
      ) : (
        <div className="stack">
          <div className="role-legend">
            {(Object.keys(ROLE_HELP) as Role[]).map((r) => <div key={r}><span className={`role role-${r}`}>{r}</span><span className="small muted">{ROLE_HELP[r]}</span></div>)}
          </div>
          <ErrorBox message={error || err} />
          <section className="panel">
            <div className="table-scroll">
            <table className="table team">
              <thead><tr><th>Person</th><th>Sign-in</th><th className="hide-sm">Last active</th><th>Role</th><th /></tr></thead>
              <tbody>
                {(users ?? []).map((u) => (
                  <tr key={u.id} style={{ cursor: "default" }}>
                    <td>
                      <span className="person"><Avatar u={u} /><span><b>{u.name}</b>{me.user?.id === u.id && <span className="faint"> (you)</span>}<span className="small muted" style={{ display: "block" }}>{u.email}</span></span></span>
                    </td>
                    <td className="small muted">{[u.has_password ? "Email" : "", u.github_login ? `GitHub @${u.github_login}` : ""].filter(Boolean).join(", ")}</td>
                    <td className="small muted hide-sm">{u.last_login_at ? ago(u.last_login_at) : "never"}</td>
                    <td>
                      <select className="select" style={{ width: "min(130px, 100%)" }} value={u.role} disabled={busy === u.id} onChange={(e) => setRole(u, e.target.value as Role)} aria-label={`Role for ${u.name}`}>
                        <option value="viewer">viewer</option><option value="member">member</option><option value="admin">admin</option>
                      </select>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {me.user?.id !== u.id && (confirm === u.id ? (
                        <span className="row confirm" style={{ justifyContent: "flex-end" }}>
                          <button className="btn sm danger" onClick={() => remove(u)} disabled={busy === u.id}>Remove</button>
                          <button className="btn sm ghost" onClick={() => setConfirm(null)}>Keep</button>
                        </span>
                      ) : <button className="btn sm ghost" onClick={() => setConfirm(u.id)}>Remove</button>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
