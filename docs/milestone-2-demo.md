# CollabPad Milestone 2 demo

## Before recording

Start the Raft Compose stack from the README. Configure the local CPU model and sign in as two Clerk users in separate browser profiles. Open a monitor terminal beside the browser. Run the following commands from the repository root and confirm the three replicas report one leader and two followers.

```bash
compose=(docker compose -f docker-compose.yml -f docker-compose.raft.yml)
watch -n 0.5 -d -x -- "${compose[@]}" exec -T \
  -e RAFT_PEERS=raft-1=raft-1:50051,raft-2=raft-2:50051,raft-3=raft-3:50051 \
  gateway sh -c 'python -m services.raft.status | sort'
```

Use `compose=(podman-compose -f docker-compose.yml -f docker-compose.raft.yml)` on Podman. Leave the monitor open and set the same `compose` array in a separate control terminal. The monitor highlights changes and keeps running through the gateway while a replica is down. Press **Ctrl+C** in the monitor terminal to close it.

## Recording outline

- **0:00–1:00** — Show the Compose stack. Explain how the browser reaches the Python gateway and how the gateway reaches the Raft leader and separate LLM service over gRPC. Show the three peer statuses.
- **1:00–2:30** — Create a private document. Enable sharing and join from the second profile. Type in both windows and show cursors, merged changes, and the **Saved** status.
- **2:30–3:30** — Open **Version history** as the owner. Preview an older edit and the latest version to show that the documents differ, then return to the editor.
- **3:30–5:30** — Identify the leader, stop it abruptly with `"${compose[@]}" kill raft-N`, and leave both tabs open. In the live monitor, show the failed replica as unavailable and point out the replacement leader and its higher term. Confirm the saved edits and history, then type and save a new edit. Run `"${compose[@]}" start raft-N` for the old leader and watch its applied index catch up in the monitor.
- **5:30–7:00** — Show a completion suggestion, a private summary, a grammar correction, and a selection enhancement. Accept one suggestion and show it in both tabs. Toggle sharing off and show the guest loses access.

## Stress probe

Keep the live monitor open and start the probe in a separate terminal with the same `compose` array. After it reports twenty acknowledged edits, kill the elected leader from the control terminal. Watch the replacement take over, then restart the old leader and wait for all three applied indexes to match.

```bash
"${compose[@]}" --profile import run --build --rm --no-deps raft-import python -m services.tests.raft_probe
```

The probe uses ten gRPC editor sessions, verifies 500 unique Yjs edits and 501 history entries, and reports acknowledgement latency.
