import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type AdminUser, type AdminWaitlistEntry, type User } from "./api";

type Filter = "pending" | "approved" | "rejected" | "all";
type Action = "approve" | "reject" | "rotate" | "revoke";
type AdminIdentity = Pick<User, "id" | "name" | "role">;

const FILTERS: Filter[] = ["pending", "approved", "rejected", "all"];

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function emailInvite(email: string, inviteUrl: string) {
  const subject = encodeURIComponent("Your Quenya tester invite");
  const body = encodeURIComponent(`Thanks for joining the Quenya test. Sign up here: ${inviteUrl}`);
  return `mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}`;
}

export default function AdminApp() {
  const [user, setUser] = useState<AdminIdentity | null>(null);
  const [authResolved, setAuthResolved] = useState(false);
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginPending, setLoginPending] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("pending");
  const [entries, setEntries] = useState<AdminWaitlistEntry[]>([]);
  const [testers, setTesters] = useState<AdminUser[]>([]);
  const [testersLoading, setTestersLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<unknown>(null);
  const [testersError, setTestersError] = useState<unknown>(null);
  const [busy, setBusy] = useState<Record<string, Action>>({});
  const [inviteLinks, setInviteLinks] = useState<Record<string, string>>({});
  const [emailInviteState, setEmailInviteState] = useState<Record<string, "sending" | "sent">>({});
  const [emailInviteErrors, setEmailInviteErrors] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState<string | null>(null);
  const [passwordPanelOpen, setPasswordPanelOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordPending, setPasswordPending] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [signOutPending, setSignOutPending] = useState(false);

  useEffect(() => {
    let active = true;
    api.me().then(me => {
      if (active) setUser(me);
    }).catch(() => {
      if (active) setUser(null);
    }).finally(() => {
      if (active) setAuthResolved(true);
    });
    return () => { active = false; };
  }, []);

  const authorized = authResolved && user?.role === "admin";

  const signIn = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoginPending(true);
    setLoginError(null);
    try {
      const result = await api.adminLogin(loginName, loginPassword);
      window.localStorage.setItem("quenya.token", result.token);
      setUser(result.user);
      setLoginPassword("");
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Unable to sign in.");
    } finally {
      setLoginPending(false);
    }
  };

  const changePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);
    if (newPassword.length < 12) {
      setPasswordError("New password must be at least 12 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match.");
      return;
    }
    setPasswordPending(true);
    try {
      await api.changeAdminPassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSuccess("Password changed.");
    } catch (error) {
      setPasswordError(error instanceof Error ? error.message : "Unable to change password.");
    } finally {
      setPasswordPending(false);
    }
  };

  const signOut = async () => {
    setSignOutPending(true);
    try {
      await api.logout();
    } catch {
      // Clear the local session even if the server cannot be reached.
    } finally {
      window.localStorage.removeItem("quenya.token");
      setUser(null);
      setPasswordPanelOpen(false);
      setSignOutPending(false);
    }
  };

  const loadWaitlist = useCallback(() => {
    if (!authorized) return;
    setListLoading(true);
    setListError(null);
    api.adminWaitlist(filter).then(setEntries).catch(error => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      } else {
        setListError(error);
      }
    }).finally(() => setListLoading(false));
  }, [authorized, filter]);

  useEffect(() => { loadWaitlist(); }, [loadWaitlist]);

  useEffect(() => {
    if (!authorized) return;
    setTestersLoading(true);
    api.adminUsers().then(rows => setTesters(rows.filter(row => row.role === "tester"))).catch(error => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      } else {
        setTestersError(error);
      }
    }).finally(() => setTestersLoading(false));
  }, [authorized]);

  const copyInvite = async (key: string, url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(key);
      window.setTimeout(() => setCopied(current => current === key ? null : current), 1600);
    } catch {
      setCopied(null);
    }
  };

  const sendInviteEmail = async (entry: AdminWaitlistEntry, inviteUrl: string) => {
    setEmailInviteState(current => ({ ...current, [entry.id]: "sending" }));
    setEmailInviteErrors(current => {
      const next = { ...current };
      delete next[entry.id];
      return next;
    });
    try {
      await api.emailInvite(entry.id, inviteUrl);
      setEmailInviteState(current => ({ ...current, [entry.id]: "sent" }));
    } catch (error) {
      setEmailInviteState(current => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
      if (error instanceof ApiError && error.status === 503) {
        window.location.href = emailInvite(entry.email, inviteUrl);
        return;
      }
      setEmailInviteErrors(current => ({
        ...current,
        [entry.id]: error instanceof Error ? error.message : "Couldn't send the email.",
      }));
    }
  };

  const review = async (entry: AdminWaitlistEntry, action: "approve" | "reject") => {
    setBusy(current => ({ ...current, [entry.id]: action }));
    try {
      if (action === "approve") {
        const result = await api.approveWaitlist(entry.id);
        setEntries(rows => rows.map(row => row.id === entry.id
          ? { ...row, status: "approved", reviewed_at: new Date().toISOString(), user_id: result.user.id }
          : row));
        setInviteLinks(current => ({ ...current, [entry.id]: result.invite_url }));
        setTesters(current => [...current, {
          id: result.user.id,
          name: result.user.name,
          role: "tester",
          created_at: new Date().toISOString(),
          revoked_at: null,
          styles: [],
          swipe_count: 0,
          last_seen_at: null,
        }]);
      } else {
        await api.rejectWaitlist(entry.id);
        setEntries(rows => rows.map(row => row.id === entry.id
          ? { ...row, status: "rejected", reviewed_at: new Date().toISOString() }
          : row));
      }
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      } else {
        setListError(error);
      }
    } finally {
      setBusy(current => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
    }
  };

  const rotate = async (tester: AdminUser) => {
    const key = `rotate:${tester.id}`;
    setBusy(current => ({ ...current, [key]: "rotate" }));
    try {
      const result = await api.rotateInvite(tester.id);
      setInviteLinks(current => ({ ...current, [key]: result.invite_url }));
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      } else {
        setTestersError(error);
      }
    } finally {
      setBusy(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  };

  const revoke = async (tester: AdminUser) => {
    if (!window.confirm(`Revoke ${tester.name}'s tester access?`)) return;
    const key = `revoke:${tester.id}`;
    setBusy(current => ({ ...current, [key]: "revoke" }));
    try {
      await api.revokeUser(tester.id);
      setTesters(rows => rows.map(row => row.id === tester.id
        ? { ...row, revoked_at: new Date().toISOString() }
        : row));
      setInviteLinks(current => {
        const next = { ...current };
        delete next[`rotate:${tester.id}`];
        return next;
      });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      } else {
        setTestersError(error);
      }
    } finally {
      setBusy(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  };

  if (!authResolved) return null;
  if (!user || user.role !== "admin") {
    return (
      <main className="admin-sign-in-stage">
        <section className="admin-sign-in-card">
          <p className="eyebrow">Quenya · Admin</p>
          <h1 className="h1">Admin sign in</h1>
          <p className="admin-sign-in-copy">Sign in to manage tester access.</p>
          <form className="admin-form" onSubmit={event => void signIn(event)}>
            <label className="field">
              <span className="lbl">Name</span>
              <input
                name="username"
                type="text"
                autoComplete="username"
                value={loginName}
                onChange={event => setLoginName(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span className="lbl">Password</span>
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                value={loginPassword}
                onChange={event => setLoginPassword(event.target.value)}
                required
              />
            </label>
            {loginError && <p className="admin-error" role="alert">{loginError}</p>}
            <button className="btn primary wide admin-sign-in-submit" type="submit" disabled={loginPending}>
              {loginPending && <span className="admin-spinner" aria-hidden="true" />}
              {loginPending ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <div className="stage admin-stage">
      <div className="phone admin-phone">
        <main className="body admin-body">
          <header className="admin-header">
            <div>
              <p className="eyebrow">Quenya · Admin</p>
              <h1 className="h1">Tester access</h1>
            </div>
            <div className="admin-header-actions">
              <button
                className="btn ghost sm"
                onClick={() => {
                  setPasswordPanelOpen(open => !open);
                  setPasswordError(null);
                  setPasswordSuccess(null);
                }}
              >
                Change password
              </button>
              <button className="btn ghost sm" onClick={() => void signOut()} disabled={signOutPending}>
                {signOutPending ? "Signing out…" : "Sign out"}
              </button>
              <a className="btn ghost sm" href="/">Back to app</a>
            </div>
          </header>

          {passwordPanelOpen && (
            <section className="admin-password-panel" aria-labelledby="admin-password-title">
              <div>
                <h2 id="admin-password-title" className="h3">Change password</h2>
                <p className="small muted">Use at least 12 characters.</p>
              </div>
              <form className="admin-form" onSubmit={event => void changePassword(event)}>
                <label className="field">
                  <span className="lbl">Current password</span>
                  <input
                    type="password"
                    autoComplete="current-password"
                    value={currentPassword}
                    onChange={event => setCurrentPassword(event.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="lbl">New password</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={event => setNewPassword(event.target.value)}
                    required
                  />
                </label>
                <label className="field">
                  <span className="lbl">Confirm new password</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={event => setConfirmPassword(event.target.value)}
                    required
                  />
                </label>
                {passwordError && <p className="admin-error" role="alert">{passwordError}</p>}
                {passwordSuccess && <p className="admin-success" role="status">{passwordSuccess}</p>}
                <button className="btn primary sm" type="submit" disabled={passwordPending}>
                  {passwordPending ? "Saving…" : "Save password"}
                </button>
              </form>
            </section>
          )}

          <section className="admin-section">
            <div className="admin-section-head">
              <div>
                <h2 className="h3">Waitlist</h2>
                <p className="small muted">Review requests before creating tester access.</p>
              </div>
              <div className="admin-filters" role="group" aria-label="Waitlist status">
                {FILTERS.map(status => (
                  <button
                    key={status}
                    className={filter === status ? "selected" : ""}
                    onClick={() => setFilter(status)}
                  >
                    {status[0].toUpperCase() + status.slice(1)}
                  </button>
                ))}
              </div>
            </div>
            {listError !== null && <p className="admin-error">{String(listError)}</p>}
            {listLoading && <p className="admin-empty">Loading waitlist…</p>}
            {!listLoading && entries.length === 0 && <p className="admin-empty">No requests in this view.</p>}
            {!listLoading && entries.map(entry => {
              const inviteUrl = inviteLinks[entry.id];
              const activeAction = busy[entry.id];
              return (
                <article className="admin-row" key={entry.id}>
                  <div className="admin-row-title">
                    <div>
                      <h3>{entry.name || entry.email}</h3>
                      <a href={`mailto:${entry.email}`}>{entry.email}</a>
                    </div>
                    <span className={`admin-status ${entry.status}`}>{entry.status}</span>
                  </div>
                  <div className="admin-details">
                    <div><span>Handle</span><b>{entry.handle || "—"}</b></div>
                    <div><span>Devices</span><b>{entry.devices || "—"}</b></div>
                    <div><span>Wants testing</span><b>{entry.wants_testing ? "Yes" : "No"}</b></div>
                    <div><span>Signed up</span><b>{dateLabel(entry.created_at)}</b></div>
                  </div>
                  {entry.style_text && <p className="admin-style">{entry.style_text}</p>}
                  {entry.status === "pending" && (
                    <div className="admin-actions">
                      <button className="btn primary sm" disabled={!!activeAction} onClick={() => void review(entry, "approve")}>
                        {activeAction === "approve" ? "Approving…" : "Approve"}
                      </button>
                      <button className="btn ghost sm" disabled={!!activeAction} onClick={() => void review(entry, "reject")}>
                        {activeAction === "reject" ? "Rejecting…" : "Reject"}
                      </button>
                    </div>
                  )}
                  {inviteUrl && (
                    <div className="admin-invite">
                      <input aria-label="Tester invite link" value={inviteUrl} readOnly />
                      <button className="btn ghost sm" onClick={() => void copyInvite(entry.id, inviteUrl)}>
                        {copied === entry.id ? "Copied" : "Copy"}
                      </button>
                      <div className="admin-email-action">
                        <button
                          className="btn ghost sm"
                          type="button"
                          disabled={emailInviteState[entry.id] === "sending" || emailInviteState[entry.id] === "sent"}
                          onClick={() => void sendInviteEmail(entry, inviteUrl)}
                        >
                          {emailInviteState[entry.id] === "sending"
                            ? "Sending…"
                            : emailInviteState[entry.id] === "sent" ? "Sent" : "Email invite"}
                        </button>
                        {emailInviteState[entry.id] === "sent" && (
                          <span className="small muted" role="status">Sent to {entry.email}</span>
                        )}
                        {emailInviteErrors[entry.id] && (
                          <span className="admin-error" role="alert">{emailInviteErrors[entry.id]}</span>
                        )}
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </section>

          <section className="admin-section">
            <div className="admin-section-head">
              <div>
                <h2 className="h3">Testers</h2>
                <p className="small muted">Manage existing tester invites and access.</p>
              </div>
            </div>
            {testersError !== null && <p className="admin-error">{String(testersError)}</p>}
            {testersLoading && <p className="admin-empty">Loading testers…</p>}
            {!testersLoading && testers.length === 0 && testersError === null && <p className="admin-empty">No testers yet.</p>}
            {!testersLoading && testers.map(tester => {
              const key = `rotate:${tester.id}`;
              const inviteUrl = inviteLinks[key];
              const active = !tester.revoked_at;
              return (
                <article className="admin-tester" key={tester.id}>
                  <div className="admin-tester-copy">
                    <b>{tester.name}</b>
                    <span>{tester.styles.length} style{tester.styles.length === 1 ? "" : "s"} · {tester.swipe_count} swipes</span>
                  </div>
                  <span className={`admin-status ${active ? "approved" : "rejected"}`}>{active ? "active" : "revoked"}</span>
                  <div className="admin-actions">
                    <button className="btn ghost sm" disabled={!active || !!busy[key]} onClick={() => void rotate(tester)}>
                      {busy[key] === "rotate" ? "Rotating…" : "Rotate"}
                    </button>
                    <button className="btn ghost sm" disabled={!active || !!busy[`revoke:${tester.id}`]} onClick={() => void revoke(tester)}>
                      {busy[`revoke:${tester.id}`] === "revoke" ? "Revoking…" : "Revoke"}
                    </button>
                  </div>
                  {inviteUrl && active && (
                    <div className="admin-invite">
                      <input aria-label="Rotated tester invite link" value={inviteUrl} readOnly />
                      <button className="btn ghost sm" onClick={() => void copyInvite(key, inviteUrl)}>
                        {copied === key ? "Copied" : "Copy"}
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        </main>
      </div>
    </div>
  );
}
