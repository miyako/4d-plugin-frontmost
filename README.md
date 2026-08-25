# 4d-plugin-frontmost

Frontmost lets a 4D application check and control its own place in the OS window/process
stack. It drives `NSRunningApplication` on macOS and the Win32 foreground-window APIs
(`GetForegroundWindow`, `SetForegroundWindow`, `ShowWindowAsync`) on Windows, and exposes
the result as a plain `LONGINT`. There is no `Picture`, `Blob`, or object return type in
this plugin — both commands operate purely on process/window activation state.

## Summary table

| Command | Returns | Purpose |
|---|---|---|
| [Is application frontmost](#is-application-frontmost) | `LONGINT` | Reports whether 4D is currently the active (frontmost) application |
| [MAKE APPLICATION FRONTMOST](#make-application-frontmost) | — | Brings 4D to the front and gives it focus |

**Platforms:** macOS (Carbon/Cocoa builds) and Windows (32-bit/64-bit).

---

## Requirements & platform notes

- Both commands take **zero parameters**. There is no optional/extended form of either command in this plugin's dispatch table.
- **On Windows**, `MAKE APPLICATION FRONTMOST` depends on a main-window handle (`gmdi`) that the plugin resolves once, at plugin init (`kInitPlugin`/`kServerInitPlugin`), and caches for the life of the process. If that resolution fails, the command has nothing to activate.
- **On Windows**, window resolution uses two strategies depending on the running 4D version: `GetMainHWND()` on newer versions, falling back to enumerating top-level windows by class name (the app's own folder name) on older ones. This is an internal detail with no user-facing syntax difference, but it means behavior on very old 4D builds is less directly verified than on current ones.
- **On macOS**, both commands map directly to `NSRunningApplication` and have no equivalent caching step — there is no init-time dependency to worry about.
- **Failure mode:** if window resolution fails on Windows, `MAKE APPLICATION FRONTMOST` does not raise a 4D error — it silently does nothing. See [Error handling & troubleshooting](#error-handling--troubleshooting).

---

## Is application frontmost

### Syntax

```4d
frontmost:=Is application frontmost
```

| Parameter | Type | Description |
|---|---|---|
| `frontmost` | `LONGINT` | Result. `1` if 4D is the active (frontmost) application, otherwise `0`. |

### Description

Returns whether 4D currently has focus at the OS level.

**On macOS**, this checks `[[NSRunningApplication currentApplication] isActive]` directly — a live, real-time check against the OS, not a cached value.

**On Windows**, this checks whether the foreground window's owning process ID matches 4D's own current process ID (`GetCurrentProcessId`), also a live check with no caching involved. This means, unlike `MAKE APPLICATION FRONTMOST`, this command does **not** depend on the `gmdi` window handle resolved at plugin init, and is unaffected by that resolution failing.

There are no parameters, and no documented failure path — the command always returns `0` or `1`.

### Example

```4d
frontmost:=Is application frontmost
If (frontmost=1)
	ALERT("4D is the active application")
Else
	ALERT("4D is running in the background")
End if
```

```4d
 // poll until the user brings 4D back to the front
While (Is application frontmost=0)
	DELAY PROCESS(Current process;60)
End while
```

---

## MAKE APPLICATION FRONTMOST

### Syntax

```4d
MAKE APPLICATION FRONTMOST
```

| Parameter | Type | Description |
|---|---|---|
| `Result` | — | None. This command has no return value and no parameters. |

### Description

Brings 4D to the front of the OS window/application stack and gives it input focus.

**On macOS**, this calls `activateWithOptions:NSApplicationActivateIgnoringOtherApps` on `NSRunningApplication`, which forces activation even if another application currently holds focus.

**On Windows**, this restores and raises the previously-cached main window handle (`gmdi`): it calls `ShowWindowAsync` (restore, if minimized), repositions it to the top of the Z-order without moving or resizing it, and calls `SetForegroundWindow`. `ShowWindowAsync` is used specifically because the window may belong to a UI thread other than the one handling the plugin call — using the synchronous `ShowWindow` here would risk blocking.

**Windows-only silent-failure case:** if `gmdi` was never successfully resolved at plugin init (see [Requirements & platform notes](#requirements--platform-notes)), this command has no window to act on. It does not raise a 4D error in that case — it simply has no visible effect. If you find this command doesn't bring 4D forward, that is the first thing to check, not a bug in the calling code.

### Example

```4d
MAKE APPLICATION FRONTMOST
```

```4d
 // bring 4D forward, then confirm it worked
MAKE APPLICATION FRONTMOST
If (Is application frontmost=0)
	ALERT("Could not bring 4D to the front")
End if
```

---

## Error handling & troubleshooting

- **`MAKE APPLICATION FRONTMOST` does nothing, on Windows only.** This means the plugin's cached main-window handle was never resolved at startup. This is a silent failure, not a 4D error — there is no exception or error code to catch. Confirm with `Is application frontmost` after calling it if you need to know whether it actually worked.
- **Behavior can differ across very old vs. current 4D versions, on Windows only.** The plugin decodes the running 4D version and switches its window-lookup strategy at a fixed threshold. This is an internal implementation detail with no effect on the command syntax, but if you're troubleshooting on an older 4D release specifically, know that the underlying lookup path is different from a current one.
- **`Is application frontmost` and `MAKE APPLICATION FRONTMOST` do not share a failure mode.** The read-only check (`Is application frontmost`) is a live OS query on both platforms and has no init-time dependency; only the write action (`MAKE APPLICATION FRONTMOST`) on Windows depends on the cached handle. Don't assume the two commands are equally reliable in every situation.
- **No macOS equivalent of the above.** On macOS both commands talk to `NSRunningApplication` directly, with no caching step and no documented failure path beyond what Cocoa itself would raise.

---

## Quick reference

```4d
 // check
frontmost:=Is application frontmost

 // activate
MAKE APPLICATION FRONTMOST

 // activate + verify (Windows-safe pattern)
MAKE APPLICATION FRONTMOST
If (Is application frontmost=0)
	 // gmdi was likely never resolved at plugin init
End if
```
