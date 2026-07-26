# WhatsApp Bridge Setup — GOWA with full history sync

Set up a local WhatsApp bridge (GOWA — `aldinokemal/go-whatsapp-web-multidevice`) that pulls historical messages via WhatsApp's history-sync handshake and exposes them as an MCP server for Claude Code. The `/job-search` skill sweeps the resulting local SQLite DB for recruiter messages; `/apply` uses it for recruiter follow-ups.

Everything stays local: your messages live in a SQLite file on your machine, and the MCP server binds to localhost only.

## When to use

- You want `/job-search` step 7 (WhatsApp recruiter scan) enabled
- The `mcp__whatsapp__*` tools fail to load or show ✗ in `claude mcp list`
- `~/gowa/` doesn't exist, or its binary is missing/outdated
- The local SQLite at `~/gowa/src/storages/chatstorage.db` is empty or corrupted
- WhatsApp shows the linked device as removed
- You want to refresh the history window (re-pairing re-runs the bootstrap)

## What this gives you

- All recent WhatsApp messages stored locally in `~/gowa/src/storages/chatstorage.db` (SQLite, queryable directly)
- Contact name lookup in `~/gowa/src/storages/whatsapp.db:whatsmeow_contacts`
- A SSE-based MCP server on `http://localhost:8080/sse` (`mcp__whatsapp__*` tools)
- Up to ~12+ months of historical chat data on first pairing (WhatsApp's server decides the actual cap)

## Prerequisites

- macOS or Linux. Tested on Darwin/arm64
- **Go 1.25.0+** (`go version`)
- Port 3000 free (REST mode) and port 8080 free (MCP mode)
- Phone with WhatsApp installed, logged into the target account, on the same network or reachable internet
- Phone setting **WhatsApp → Settings → Chats → Chat History → "Include Recent Messages"** must be ON. Without this, no history will sync regardless of patch values.

## Steps

### 1. Locate or clone the source

If `~/gowa/` exists with a `src/` subdirectory, use it. Otherwise:

```bash
git clone https://github.com/aldinokemal/go-whatsapp-web-multidevice.git ~/gowa
```

### 2. Apply the HistorySyncConfig patch

**Critical: without this patch, WhatsApp will not send `INITIAL_BOOTSTRAP` messages.** GOWA upstream sets only `PlatformType` and `Os` on `store.DeviceProps`. Without a `HistorySyncConfig` field, WhatsApp's server falls back to sending only `PUSH_NAME` and `INITIAL_STATUS_V3` payloads — no actual conversation history.

The patch lives in two files. Apply identical changes to both `configureDeviceProps()` in `device_manager.go` and `InitWaCLI()` in `init.go`:

```diff
 import (
     ...
     "go.mau.fi/whatsmeow"
+    "go.mau.fi/whatsmeow/proto/waCompanionReg"
     "go.mau.fi/whatsmeow/store"
     ...
 )
```

After the existing `store.DeviceProps.Os = &osName` line in each function, append:

```go
fullSyncDays := uint32(14)
fullSyncSizeMb := uint32(1024)
storageQuotaMb := uint32(1024)
store.DeviceProps.HistorySyncConfig = &waCompanionReg.DeviceProps_HistorySyncConfig{
    FullSyncDaysLimit:   &fullSyncDays,
    FullSyncSizeMbLimit: &fullSyncSizeMb,
    StorageQuotaMb:      &storageQuotaMb,
}
```

Files to edit:
- `~/gowa/src/infrastructure/whatsapp/device_manager.go` — around line 605 (inside `func configureDeviceProps()`)
- `~/gowa/src/infrastructure/whatsapp/init.go` — around line 80 (inside `func InitWaCLI()`)

**Why 14 / 1024 / 1024 specifically:** larger values trigger WhatsApp's anti-abuse heuristics — they're rejected and the link fails with a vague "try again later" error on the phone. These conservative values are known-good. In practice WhatsApp ignores `FullSyncDaysLimit=14` anyway and pushes much more (22 months of history has come back in testing). Treat the values as a "permission to sync" signal, not a precise limit.

### 3. Build the binary

```bash
cd ~/gowa/src && go build -o whatsapp .
```

~5 seconds with cache. Output: `~/gowa/src/whatsapp` (~43 MB).

### 4. Verify port 3000 is free and clean any stale state

```bash
lsof -iTCP:3000 -sTCP:LISTEN
```

If something is bound to 3000, kill it (`kill -9 $(lsof -ti :3000)`) — but be sure it's not a real service you care about. Stale browser connections will appear as ESTABLISHED but aren't blockers; only LISTEN entries block bind.

For a clean re-pair, wipe any prior pairing state:

```bash
cd ~/gowa/src/storages && /bin/rm -f whatsapp.db whatsapp.db-shm whatsapp.db-wal chatstorage.db chatstorage.db-shm chatstorage.db-wal history-*.json
```

**Skip the wipe if you want to preserve the existing message cache.** Re-pairing without wiping will fail because the old device record blocks fresh pairing — but you can keep `chatstorage.db` (move it aside, re-pair, then restore later if needed). The conservative path is: wipe everything and re-sync, accepting that recent messages re-arrive automatically.

### 5. Start REST mode and pair via QR

```bash
cd ~/gowa/src && nohup ./whatsapp rest > /tmp/gowa-rest.log 2>&1 &
disown
```

Verify it's listening: `curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:3000/` should print `HTTP 200`.

Open `http://localhost:3000` in a browser. Click the device-link button to show a QR. On the phone: **WhatsApp → Settings → Linked Devices → Link a Device** → scan.

### 6. Wait for `INITIAL_BOOTSTRAP` (5–15 min)

Watch the log:

```bash
tail -f /tmp/gowa-rest.log | grep -E --line-buffered "LOGIN_SUCCESS|Processing history sync|Wrote history sync|level=fatal"
```

Expected sequence:
1. `LOGIN_SUCCESS Successfully pair with <your-number>@s.whatsapp.net` — pairing accepted
2. Within ~30s–2min: history JSON files start appearing in `~/gowa/src/storages/`:
   - `history-*-1-PUSH_NAME.json` — contact names
   - `history-*-2-INITIAL_STATUS_V3.json` — status updates (skipped by code, normal)
   - `history-*-N-INITIAL_BOOTSTRAP.json` — **the moneymaker, real conversation messages**
   - `history-*-N-RECENT.json` (typically 5–10 of these, ~5MB each) — additional batches

Watch the DB grow:

```bash
sqlite3 ~/gowa/src/storages/chatstorage.db "SELECT COUNT(*) FROM messages;"
```

A healthy sync ends with thousands of messages and hundreds of chats.

### 7. (Optional) Switch REST → MCP mode

REST mode runs a UI on `:3000`. MCP mode runs an SSE server on `:8080` that Claude Code consumes. For ongoing use, MCP is the right mode.

```bash
pkill -f "gowa/src/whatsapp"
sleep 1
cd ~/gowa/src && nohup ./whatsapp mcp > /tmp/gowa-mcp.log 2>&1 &
disown
```

Note: this kills any other GOWA process too. The SSE endpoint becomes available at `http://localhost:8080/sse`.

### 8. Register the MCP server with Claude Code

If it's already registered (`claude mcp list | grep whatsapp` shows it):

```bash
claude mcp list | grep whatsapp
# whatsapp: http://localhost:8080/sse (SSE) - ✓ Connected
```

If not:

```bash
claude mcp add --scope user --transport sse whatsapp http://localhost:8080/sse
```

The user-scope entry is stored in `~/.claude.json`. Restart the current Claude Code session (`/exit` then reopen) for the MCP tools to load.

### 9. (Optional) Persist with launchd (macOS)

Without this, the bridge dies if the Terminal closes, on logout, on reboot, or on any crash. To make it permanent (launchd requires absolute paths — no `~`; replace `/Users/<you>` with your home directory):

```bash
cat > ~/Library/LaunchAgents/com.gowa.mcp.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gowa.mcp</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/<you>/gowa/src/whatsapp</string>
        <string>mcp</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/<you>/gowa/src</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/<you>/Library/Logs/gowa-mcp.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<you>/Library/Logs/gowa-mcp.log</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.gowa.mcp.plist
```

Verify: `launchctl list | grep gowa` should show the service with a non-zero PID. Test resilience: `pkill -f "gowa/src/whatsapp"` and confirm a new PID appears within ~5s.

To remove later: `launchctl unload ~/Library/LaunchAgents/com.gowa.mcp.plist && /bin/rm ~/Library/LaunchAgents/com.gowa.mcp.plist`.

### 10. Enable in config.yaml

Back in the claude-job-search repo, set:

```yaml
integrations:
  whatsapp:
    enabled: true
    chat_db: ~/gowa/src/storages/chatstorage.db
    contacts_db: ~/gowa/src/storages/whatsapp.db
```

## Verification checklist

- `claude mcp list` shows `whatsapp: ... ✓ Connected`
- `sqlite3 ~/gowa/src/storages/chatstorage.db "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM messages;"` returns a long time span and thousands of messages
- `ls ~/gowa/src/storages/history-*INITIAL_BOOTSTRAP.json` returns at least one file
- Reopening Claude Code surfaces `mcp__whatsapp__*` tools (via ToolSearch keyword `"whatsapp"`)

## Troubleshooting

**Phone shows "try again later" after QR scan:** WhatsApp rate-limit on linked-device pairing. Wait 30–60 minutes (sometimes a few hours), then retry. Also check linked-device count on phone — max 4. If you're using aggressive HistorySyncConfig values (>14 days, >1GB limits), reduce them — anti-abuse can also surface here.

**`level=fatal Failed to listen: ... bind: address already in use`:** Another process is bound to port 3000 (REST) or 8080 (MCP). Find it with `lsof -iTCP:3000 -sTCP:LISTEN` or `lsof -iTCP:8080 -sTCP:LISTEN`, kill if appropriate.

**Pairing succeeds but only `PUSH_NAME` and `INITIAL_STATUS_V3` files appear, no `INITIAL_BOOTSTRAP`:** The patch wasn't applied or didn't compile in. Re-check both files, rebuild, wipe state, re-pair. Confirm by `grep HistorySyncConfig ~/gowa/src/infrastructure/whatsapp/*.go` — should return two hits.

**Bootstrap arrived but DB has < 100 messages:** Phone setting "Include Recent Messages" is OFF, or the account has very little history. Check the phone setting and re-pair.

**`QR context canceled` repeatedly in log without `LOGIN_SUCCESS`:** Either the phone didn't scan in time (~3 min QR expiry), or WhatsApp rejected the link (rate-limit or DeviceProps issue). Refresh the page to generate a fresh QR; if it keeps failing, see the rate-limit note above.

## Persistence guarantees

- **Messages**: `~/gowa/src/storages/chatstorage.db` — SQLite, survives crashes/reboots
- **Auth**: `~/gowa/src/storages/whatsapp.db` — device session keys; wiping forces re-pair
- **History dumps**: `~/gowa/src/storages/history-*.json` — JSON backups of each sync batch, can be re-processed if DB is rebuilt
- **MCP registration**: `~/.claude.json` (user scope) — survives Claude Code reinstalls

Backup recommendation: periodically copy `~/gowa/src/storages/chatstorage.db` to a safe location. The auth DB (`whatsapp.db`) can also be backed up if you want to avoid re-pairing, but it's tied to one device session.

## Upstream

https://github.com/aldinokemal/go-whatsapp-web-multidevice (fork locally and apply the HistorySyncConfig patch above)
