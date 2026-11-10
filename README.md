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

## Manual versioning and publication

**Bump version** and **Build** are independent manual Actions. Neither runs on
pushes, tags, or pull requests. In the Actions UI, explicitly select the branch
for Bump version and choose patch, minor, or major. It validates the immutable
dispatch SHA, a clean checkout, and coordinated source metadata, then commits
only that metadata to the selected branch. A normal, non-force push disables
tag following explicitly. A required pre-push hook checks the receive-pack
advertisement against the exact selected old SHA; advances, ancestor rewinds,
deletions, and same-result races are rejected, including after the last
`ls-remote` check. A missing/nonexecutable guard fails closed. No tag or Build
is triggered. If the branch moves, inspect the failure and dispatch again.

Run **Build** separately on the desired ref; it has **no inputs**. All three
checkouts use the same immutable `github.sha`. Its strict `X.Y.Z` comes from
source, not a tag: all six `MARKETING_VERSION` and coordinated
`CURRENT_PROJECT_VERSION` settings, `Info.plist`'s short version and optional
bundle version, and both English localized version strings must agree.
Supported Xcode version-variable references are preserved, as are absent
optional plist keys. Prebuilt example bundles, `manifest.json`, localization
revision counters, dependency versions, and 4D/schema metadata are not release
version authorities.

The old bump header promised project/marketing coordination but its implementation
only updated marketing settings. This migration normalizes the six stale
`CURRENT_PROJECT_VERSION` values (`1.0.3`) and English overrides (`1.0`) to
the existing marketing/plist authority **1.0.5**, without a release-version
increment. The absent `CFBundleVersion` remains absent.

Build retains Windows x64, macOS arm64+x86_64, the merged bundle, signing,
both ZIP/DMG notarizations and DMG stapling. Assets remain
`Frontmost-X.Y.Z.zip` and `Frontmost-X.Y.Z.dmg`. Publication is serialized
repository-wide across all branches. Before native work and again after all
native/signing/notary/stapling gates, it checks remote tags and authenticates a
paginated release listing, including drafts. Any API/auth/network/404/schema
error, including on later pages, fails closed; only a complete successful
no-match permits publication.

Publication creates a new lightweight `vX.Y.Z` tag at the selected SHA through
REST POST (even same-SHA concurrent creation fails), verifies the remote SHA,
then POSTs a public, non-prerelease release with explicit tag/target/name and
empty, non-generated notes, matching the original notes policy. Assets are
uploaded separately to the newly returned release ID, without replacement.
There is no update-existing, delete, rollback, or automatic retry.

**Failure recovery:** partial state is deliberately retained. A failed release
POST can leave a tag; an upload failure can leave a public release with zero or
some assets. A response lost in transit can also mean the server succeeded.
Inspect the remote tag, release (including drafts), assets and Actions log
manually before taking action. Do not rerun expecting repair: existing state
blocks a rerun. Recover manually under repository policy or use a newly bumped
version; the workflows never remove or overwrite prior publication state.

Local lifecycle checks (no native builds or signing):
`python3 -m unittest discover -s .github/tests -v`.

## Native ownership regression

The bounded audit at `5db961c1523697688905549f04f4a19281dedf6b` found
ownership defects in the compiled but dispatcher-dormant `CPicture` helpers.
`fromFileAtPath` now clears its shallow-transferred path only through the
argument owner, and releases the output argument after `setPicture` retains
it. `setBytes` releases its temporary after the same retain. Destinations
overwritten by shallow string/picture setters start as Undefined rather than
leaking allocated empty values. The genuinely owning output arguments still
start as Picture. Synchronous borrowed input detachment and owning picture
returns are preserved. `createCopy` converts the SDK's returned byte count
to UTF-16 code units before constructing a string.

**Inventory and reachability:** the tracked native surface consists of
`4DPlugin.cpp/.h`, `4D Plugin_Prefix.pch`, the bundled SDK
`4DPluginAPI.c/.h/.def`, `EntryPoints.h`, `Flags.h`, `PrivateTypes.h`,
`PublicTypes.h`, and all 16 wrapper implementation/header pairs plus
`Classes/C_TYPES.h`. The wrappers are `ARRAY_BOOLEAN`, `ARRAY_DATE`,
`ARRAY_INTEGER`, `ARRAY_LONGINT`, `ARRAY_REAL`, `ARRAY_TEXT`, `ARRAY_TIME`,
`C_BLOB` (including `CBytes`), `C_DATE`, `C_INTEGER`, `C_LONGINT`,
`C_PICTURE` (including `CPicture`), `C_POINTER`, `C_REAL`, `C_TEXT`, and
`C_TIME`. Both Visual Studio projects and Xcode's source phase compile all
these implementations; the umbrella SDK header includes every wrapper.
`FourDPackex` in the SDK is the exported entry (also named by the Windows
`.def`), calling `PluginMain` and the real `CommandDispatcher`. Selectors 1
and 2 implement the two zero-argument manifest commands; only `C_LONGINT`
is called by them. Windows init also uses SDK application-path/version
helpers. The source and example-bundle manifests agree; tracked prebuilt
bundles are not rebuilt or certified by this audit.

**Bundled SDK contracts:** the core file's SHA-256 is
`cbcab37e636e4fa2ad6793d419280f19261b0bf8cc8c3fe24747ea1a83229d0d`.
It was placed at its current path in
`a19686625df8d371f9006d8780d4d8579cc30052`; the v13 header/v14
implementation comments are not a verified upstream release identity.
Its bytes also match `frontmost/4D Plugin API/4DPluginAPI.c` in the
initial repository commit `9d64a99e535d650dd497916f21be2d87403cf24b`,
not that commit's separate `frontmost/v11` SDK.
No upstream SDK revision is pinned by a dependency manifest.
The unchanged implementation shallow-assigns scalar string/picture values,
allocates empty owners when creating those variable kinds, forwards picture
retains/releases to the engine, and documents charset conversion lengths
as bytes. `PA_SetStringInArray` disposes/replaces the old element only for
Unicode array kind, non-null data, index in `0..fNbElements`, and non-null
input descriptor. Rejection retains the caller's owner; the getter borrows
the element. `ARRAY_TEXT` independently copies input and allocates each
output; no unconditional post-transfer disposal was present.
Scalar `C_TEXT` uses a content setter, not descriptor transfer.
Numeric/date/time wrappers and BLOB encoding helpers introduce no additional
SDK string owner. This SDK has no object/collection getter surface.
Command handle-allocation failure returns raw kind 0, even though the
normalizing kind getter labels it as text; method failure is Undefined.
The CPicture commands used here are procedures, and their returned sentinels
are not adopted or cleared.

Run the macOS synthetic regression with installed Xcode command-line tools:

```sh
python3 .github/tests/native/run.py
```

It directly compiles the **full unchanged bundled SDK**, all production
wrappers and dispatcher under ASan/UBSan, using only synthetic engine
fixtures. `NSRunningApplication` is replaced before compilation: no real
activation, desktop enumeration, application launch, or user files are used.
The 30-case matrix covers ASCII, BMP, supplementary and empty text; allocated
and null empties; exact charset byte/unit/capacity/termination boundaries;
array copying, independent transfer, replacement, shrink, clear and
rejections; scalar copies; picture retains, handoffs surviving wrapper
destruction, repeated replacement, command/handle failures, and exported
dispatcher behavior. Explicit string/picture/handle balances complement
ASan/UBSan because macOS LSan is unavailable.

To reproduce the before/after comparison, extract an immutable
`git archive 5db961c1523697688905549f04f4a19281dedf6b` into a temporary
directory, then run this same final harness:

```sh
python3 .github/tests/native/run.py --source /path/to/archive \
  --expect-baseline-failures --preserve-ref 5db961c1523697688905549f04f4a19281dedf6b
python3 .github/tests/native/run.py \
  --preserve-ref 5db961c1523697688905549f04f4a19281dedf6b
```

The baseline fails the named double-disposal, overwritten-empty-owner,
retained-output/temporary picture-balance, and charset heap-overread
assertions; the fixed sources pass all cases. Preservation compares every
pre-existing tracked file, allowing only the intentional `C_PICTURE.cpp`
and README edits on the fixed side. SDK internals, other wrappers,
projects/manifests, workflows and bundled binaries remain byte-identical.

**Limits and deferrals:** this is not execution in real 4D or Windows, nor a
reproduction/certification of the originating Windows Poppler crash.
`C_POINTER`/`PA_GetPointerValue` metadata and SDK engine-only application-path
and picture-data output ownership remain ambiguous and unchanged. The shim
does not adopt picture-data output, and failed outputs are not treated as
valid owners. Normal array tests do not certify SDK resize-OOM count
corruption; unrelated allocation-failure hardening and SDK rewrites are
deferred. No signing, packaging, releases, manual Actions or native project
builds are required for these checks.

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
