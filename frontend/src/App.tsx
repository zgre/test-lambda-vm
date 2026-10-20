import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Entry, type Project } from "./api";
import { logout, userEmail } from "./auth";
import {
  addDays, dayKey, formatDay, formatDuration, formatHours, formatTime,
  fromLocalInput, startOfWeek, toApi, toLocalInput,
} from "./time";

type Tab = "timesheet" | "projects" | "reports";

export default function App() {
  const [tab, setTab] = useState<Tab>("timesheet");
  const [projects, setProjects] = useState<Project[]>([]);
  const [running, setRunning] = useState<Entry | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped whenever entries change so lists and reports refetch.
  const [version, setVersion] = useState(0);
  const changed = useCallback(() => setVersion((v) => v + 1), []);

  const guard = useCallback(async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const reloadProjects = useCallback(() => guard(async () => setProjects(await api.listProjects())), [guard]);

  useEffect(() => {
    reloadProjects();
    guard(async () => setRunning((await api.getTimer()).running));
  }, [guard, reloadProjects]);

  const byId = useMemo(() => new Map(projects.map((p) => [p.id, p])), [projects]);

  return (
    <div className="app">
      <header className="top">
        <h1>Time Tracker</h1>
        <nav>
          {(["timesheet", "projects", "reports"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
        <div className="user">
          <span>{userEmail()}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </header>

      {error && (
        <div className="error" role="alert">
          {error} <button className="link" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <TimerBar
        projects={projects.filter((p) => !p.archived)}
        running={running}
        byId={byId}
        onStart={(projectId, description) =>
          guard(async () => {
            setRunning((await api.startTimer(projectId, description)).running);
            changed();
          })
        }
        onStop={() =>
          guard(async () => {
            await api.stopTimer();
            setRunning(null);
            changed();
          })
        }
      />

      <main>
        {tab === "timesheet" && (
          <Timesheet projects={projects} byId={byId} version={version} guard={guard}
            onChanged={() => {
              changed();
              guard(async () => setRunning((await api.getTimer()).running));
            }} />
        )}
        {tab === "projects" && <Projects projects={projects} guard={guard} reload={reloadProjects} />}
        {tab === "reports" && <Reports byId={byId} version={version} guard={guard} />}
      </main>
    </div>
  );
}

type Guard = (fn: () => Promise<unknown>) => Promise<void>;

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function Swatch({ color }: { color?: string }) {
  return <span className="swatch" style={{ background: color ?? "#999" }} />;
}

// ---- timer ----------------------------------------------------------------

function TimerBar(props: {
  projects: Project[];
  running: Entry | null;
  byId: Map<string, Project>;
  onStart: (projectId: string, description: string) => void;
  onStop: () => void;
}) {
  const { projects, running, byId } = props;
  const [projectId, setProjectId] = useState("");
  const [description, setDescription] = useState("");
  const now = useNow(running !== null);

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [projects, projectId]);

  if (running) {
    const elapsed = Math.max(0, Math.floor((now - new Date(running.startedAt).getTime()) / 1000));
    const p = byId.get(running.projectId);
    return (
      <section className="timer running">
        <div className="timer-info">
          <Swatch color={p?.color} />
          <strong>{p?.name ?? "Unknown project"}</strong>
          <span className="muted">{running.description}</span>
        </div>
        <span className="clock">{formatDuration(elapsed)}</span>
        <button className="stop" onClick={props.onStop}>Stop</button>
      </section>
    );
  }

  if (!projects.length) {
    return <section className="timer muted">Create a project to start tracking time.</section>;
  }

  return (
    <form className="timer" onSubmit={(e) => {
      e.preventDefault();
      props.onStart(projectId, description);
      setDescription("");
    }}>
      <input className="grow" placeholder="What are you working on?" value={description}
        onChange={(e) => setDescription(e.target.value)} maxLength={500} />
      <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <button className="start" type="submit">Start</button>
    </form>
  );
}

// ---- timesheet ------------------------------------------------------------

function Timesheet(props: {
  projects: Project[];
  byId: Map<string, Project>;
  version: number;
  guard: Guard;
  onChanged: () => void;
}) {
  const { byId, version, guard } = props;
  const [week, setWeek] = useState(() => startOfWeek(new Date()));
  const [entries, setEntries] = useState<Entry[]>([]);
  const [editing, setEditing] = useState<Entry | "new" | null>(null);

  useEffect(() => {
    guard(async () => setEntries(await api.listEntries(toApi(week), toApi(addDays(week, 7)))));
  }, [week, version, guard]);

  const days = useMemo(() => {
    const groups = new Map<string, Entry[]>();
    for (const e of entries) {
      const k = dayKey(e.startedAt);
      groups.set(k, [...(groups.get(k) ?? []), e]);
    }
    return [...groups.entries()];
  }, [entries]);

  const total = entries.reduce((s, e) => s + e.seconds, 0);

  return (
    <section>
      <div className="toolbar">
        <button onClick={() => setWeek(addDays(week, -7))} aria-label="Previous week">←</button>
        <strong>Week of {week.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}</strong>
        <button onClick={() => setWeek(addDays(week, 7))} aria-label="Next week">→</button>
        <button className="link" onClick={() => setWeek(startOfWeek(new Date()))}>This week</button>
        <span className="spacer" />
        <span className="muted">Total {formatHours(total)}</span>
        <button onClick={() => setEditing("new")} disabled={!props.projects.length}>Add entry</button>
      </div>

      {editing && (
        <EntryForm
          entry={editing === "new" ? null : editing}
          projects={props.projects.filter((p) => !p.archived || (editing !== "new" && p.id === editing.projectId))}
          onCancel={() => setEditing(null)}
          onSave={(data) => guard(async () => {
            if (editing === "new") await api.createEntry(data);
            else await api.updateEntry(editing.id, editing.endedAt ? data : { ...data, endedAt: undefined });
            setEditing(null);
            props.onChanged();
          })}
        />
      )}

      {!days.length && <p className="muted empty">No time tracked this week.</p>}
      {days.map(([key, list]) => (
        <div key={key} className="day">
          <div className="day-head">
            <span>{formatDay(key)}</span>
            <span className="muted">{formatDuration(list.reduce((s, e) => s + e.seconds, 0))}</span>
          </div>
          {list.map((e) => {
            const p = byId.get(e.projectId);
            return (
              <div key={e.id} className="entry">
                <Swatch color={p?.color} />
                <span className="entry-project">{p?.name ?? "Unknown"}</span>
                <span className="grow">{e.description || <span className="muted">No description</span>}</span>
                <span className="muted">
                  {formatTime(e.startedAt)} – {e.endedAt ? formatTime(e.endedAt) : "now"}
                </span>
                <span className="duration">{formatDuration(e.seconds)}</span>
                <button className="link" onClick={() => setEditing(e)}>Edit</button>
                <button className="link danger" onClick={() => guard(async () => {
                  await api.deleteEntry(e.id);
                  props.onChanged();
                })}>Delete</button>
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}

function EntryForm(props: {
  entry: Entry | null;
  projects: Project[];
  onCancel: () => void;
  onSave: (data: { projectId: string; description: string; startedAt: string; endedAt: string }) => void;
}) {
  const { entry } = props;
  const running = entry !== null && entry.endedAt === null;
  const defaultEnd = new Date();
  const defaultStart = new Date(defaultEnd.getTime() - 3600_000);
  const [projectId, setProjectId] = useState(entry?.projectId ?? props.projects[0]?.id ?? "");
  const [description, setDescription] = useState(entry?.description ?? "");
  const [start, setStart] = useState(toLocalInput(entry?.startedAt ?? defaultStart));
  const [end, setEnd] = useState(toLocalInput(entry?.endedAt ?? defaultEnd));
  const invalid = !start || (!running && (!end || new Date(end) < new Date(start)));

  return (
    <form className="card form" onSubmit={(e) => {
      e.preventDefault();
      props.onSave({ projectId, description, startedAt: fromLocalInput(start), endedAt: fromLocalInput(end) });
    }}>
      <h3>{entry ? "Edit entry" : "Add entry"}</h3>
      <label>Project
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {props.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label>Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
      </label>
      <div className="row">
        <label>Start<input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} required /></label>
        {!running && (
          <label>End<input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} required /></label>
        )}
      </div>
      {invalid && start && end && <p className="error-text">End must be after start.</p>}
      <div className="row">
        <button type="submit" disabled={invalid || !projectId}>Save</button>
        <button type="button" className="link" onClick={props.onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// ---- projects -------------------------------------------------------------

const PALETTE = ["#4f7cff", "#16a34a", "#ea580c", "#9333ea", "#db2777", "#0891b2", "#ca8a04", "#64748b"];

function Projects(props: { projects: Project[]; guard: Guard; reload: () => Promise<void> }) {
  const { projects, guard, reload } = props;
  const [name, setName] = useState("");
  const [color, setColor] = useState(PALETTE[0]);

  return (
    <section>
      <form className="toolbar" onSubmit={(e) => {
        e.preventDefault();
        guard(async () => {
          await api.createProject(name.trim(), color);
          setName("");
          setColor(PALETTE[(projects.length + 1) % PALETTE.length]);
          await reload();
        });
      }}>
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label="Project color" />
        <input className="grow" placeholder="New project name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
        <button type="submit" disabled={!name.trim()}>Add project</button>
      </form>

      {!projects.length && <p className="muted empty">No projects yet.</p>}
      {projects.map((p) => (
        <div key={p.id} className={p.archived ? "entry archived" : "entry"}>
          <input type="color" value={p.color} aria-label={`${p.name} color`}
            onChange={(e) => guard(async () => { await api.updateProject(p.id, { color: e.target.value }); await reload(); })} />
          <span className="grow">{p.name}{p.archived && <span className="muted"> (archived)</span>}</span>
          <button className="link" onClick={() => {
            const next = window.prompt("Rename project", p.name);
            if (next && next.trim()) guard(async () => { await api.updateProject(p.id, { name: next.trim() }); await reload(); });
          }}>Rename</button>
          <button className="link" onClick={() => guard(async () => {
            await api.updateProject(p.id, { archived: !p.archived });
            await reload();
          })}>{p.archived ? "Unarchive" : "Archive"}</button>
          <button className="link danger" onClick={() => guard(async () => { await api.deleteProject(p.id); await reload(); })}>
            Delete
          </button>
        </div>
      ))}
      <p className="muted hint">Projects with time entries can't be deleted. Archive them to hide them from the timer.</p>
    </section>
  );
}

// ---- reports --------------------------------------------------------------

function Reports(props: { byId: Map<string, Project>; version: number; guard: Guard }) {
  const { byId, version, guard } = props;
  const [from, setFrom] = useState(() => toLocalInput(startOfWeek(new Date())).slice(0, 10));
  const [to, setTo] = useState(() => toLocalInput(addDays(startOfWeek(new Date()), 6)).slice(0, 10));
  const [entries, setEntries] = useState<Entry[]>([]);

  useEffect(() => {
    if (!from || !to || to < from) return;
    const start = toApi(new Date(`${from}T00:00:00`));
    const end = toApi(new Date(`${to}T23:59:59`));
    guard(async () => setEntries(await api.listEntries(start, end)));
  }, [from, to, version, guard]);

  // Grouped client-side so days follow the viewer's timezone.
  const { total, perProject, perDay } = useMemo(() => {
    const perProject = new Map<string, number>();
    const perDay = new Map<string, number>();
    let total = 0;
    for (const e of entries) {
      total += e.seconds;
      perProject.set(e.projectId, (perProject.get(e.projectId) ?? 0) + e.seconds);
      const k = dayKey(e.startedAt);
      perDay.set(k, (perDay.get(k) ?? 0) + e.seconds);
    }
    return {
      total,
      perProject: [...perProject.entries()].sort((a, b) => b[1] - a[1]),
      perDay: [...perDay.entries()].sort(),
    };
  }, [entries]);

  const max = Math.max(1, ...perProject.map(([, s]) => s));
  const exportCsv = () => {
    const rows = [["date", "project", "description", "start", "end", "hours"]];
    for (const e of [...entries].reverse()) {
      rows.push([dayKey(e.startedAt), byId.get(e.projectId)?.name ?? "", e.description, e.startedAt,
        e.endedAt ?? "", (e.seconds / 3600).toFixed(2)]);
    }
    const csv = rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `time-${from}-to-${to}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section>
      <div className="toolbar">
        <label>From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <span className="spacer" />
        <strong>Total {formatHours(total)}</strong>
        <button onClick={exportCsv} disabled={!entries.length}>Export CSV</button>
      </div>

      <div className="report-grid">
        <div className="card">
          <h3>By project</h3>
          {!perProject.length && <p className="muted">No time in this range.</p>}
          {perProject.map(([id, secs]) => {
            const p = byId.get(id);
            return (
              <div key={id} className="bar-row">
                <span className="bar-label"><Swatch color={p?.color} />{p?.name ?? "Unknown"}</span>
                <span className="bar-track">
                  <span className="bar" style={{ width: `${(secs / max) * 100}%`, background: p?.color }} />
                </span>
                <span className="duration">{formatHours(secs)}</span>
              </div>
            );
          })}
        </div>
        <div className="card">
          <h3>By day</h3>
          {perDay.map(([day, secs]) => (
            <div key={day} className="bar-row">
              <span className="grow">{formatDay(day)}</span>
              <span className="duration">{formatHours(secs)}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
