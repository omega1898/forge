// Run by tests/test_extension.py against a real FORGE API server.
import assert = require("node:assert/strict");
import test = require("node:test");
import { ForgeApi } from "../src/client";
import { CursorDecision, EventCursor } from "../src/sse";
import { itemIds, viewFromSummary } from "../src/view";

const url = process.env.FORGE_URL;
const token = process.env.FORGE_TOKEN;
const projectId = process.env.FORGE_PROJECT_ID;
const budget = { max_wall_ms: 60000, max_attempts: 2 };

test("reconnect recovers missed events exactly once; the tree matches the record", async () => {
  if (!url || !token || !projectId) return;           // only meaningful against a live server
  const api = new ForgeApi({ url, token });
  const cursor = new EventCursor(0);
  const applied: number[] = [];
  const record = (d: CursorDecision) => { if (d.action === "apply") applied.push(d.seq); };

  // First connection: read the backlog, then drop the connection.
  const first = new AbortController();
  const reading = api.follow(cursor, record, first.signal);
  await new Promise<void>((resolve) => setTimeout(resolve, 600));
  first.abort();
  await reading;
  const before = cursor.lastSeq;
  assert.ok(before >= 1);

  // While disconnected, the record changes.
  for (const title of ["Parse tag records", "Reject truncated tags"]) {
    await api.mutate("task.create", projectId, { task_type: "execute", title, budget }, `ext-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`);
  }

  // Reconnect: the server resumes after Last-Event-ID; nothing is missed or repeated.
  const second = new AbortController();
  const resumed = api.follow(cursor, record, second.signal);
  await new Promise<void>((resolve) => setTimeout(resolve, 800));
  second.abort();
  await resumed;
  assert.ok(cursor.lastSeq > before);
  assert.deepEqual(applied, Array.from({ length: applied.length }, (_, i) => i + 1));

  const summary = await api.call("project.summary", { project_id: projectId });
  process.stdout.write(`FORGE-EXTENSION ${JSON.stringify({ applied, ids: itemIds(viewFromSummary(summary)),
    as_of: summary.as_of_event_seq })}\n`);
});
