# Memcard — Deck simulator for the Decky plugin

Status: **proposed 2026-11-19, future feature.** Not approved or scheduled. Progress will be tracked
in `tasks/todo.md` under **Next** once it is.

---

## 1. Why

The Decky plugin is the part of Memcard with the least coverage before it reaches hardware:

- The pure parts are tested with `node --test`: the launch state machine (`launchController.ts`)
  and the wording modules (`conflictText.ts`, `historyText.ts`, `updates.ts`). The backend runner
  is tested with pytest.
- Everything else only runs for the first time on a Deck. That covers the screens, how they react
  to the CLI's real output, and the order of calls in a restore or a launch. It's most of
  `docs/install.md` §8.

The #18 review found three bugs of exactly this kind by reading the code:

- "Restored" showed after a restore whose download had failed.
- The panel didn't refresh after a refused restore.
- Two saves could show the same name.

Each would have been obvious the first time someone clicked through the flow against a real
backend.

**The goal:** run the real plugin code against the real `memcard` CLI on a Mac or in CI, click
through it, and script the important flows. A Deck round then becomes a final check, not the first
time the code runs.

**Not the goal:** replacing the Deck. See §7 for what only hardware can tell us.

## 2. Things that shape the design

- **The plugin touches the outside world in three places.**
  - `@decky/ui`, for components. In use today:
    - `PanelSection`, `PanelSectionRow`
    - `ButtonItem`, `DialogButton`
    - `ModalRoot`, `showModal`, `ConfirmModal`
    - `Field`, `Focusable`, `TextField`
    - `ToggleField`, `Dropdown`, `Spinner`
    - `staticClasses`, `Router`
  - `@decky/api`:
    - `definePlugin`, `callable`, `toaster`, `openFilePicker`
  - Steam's globals:
    - `SteamClient.Apps.RegisterForGameActionStart`, `CancelGameAction` and `RunGame`
    - `SteamClient.GameSessions.RegisterForAppLifetimeNotifications`
    - `Router.RunningApps`
    - `appStore.GetAppOverviewByAppID`
    - `window.DeckyBackend.call("utilities/install_plugin", …)` for updates
- **Everything else is our own code.** Replacing just those three places runs the rest unchanged.
- **The panel needs no source changes.** `index.tsx` default-exports `definePlugin(() => ({ content, … }))`.
  A stand-in `definePlugin` hands that object to the page, and the page renders `content` the way
  Decky would.
- **The backend is one Python class.** `main.py`'s `Plugin` reads five things from the `decky`
  module and passes every call to `runner.py`, which runs `memcard --json`. A fake `decky` module
  lets the simulator use the real `main.py` and `runner.py`, not a copy.
- **macOS's `/usr/bin/python3` is 3.9.** The runner prefers it (`system_python()`), and Memcard
  needs 3.11 or newer. So the simulator sets `runner.python` to its own interpreter.
- **The integration tests already run a real cloud locally.** `core/tests/conftest.py` starts
  `rclone serve webdav` on a temp folder. The same server makes a realistic default cloud, with no
  credentials, so it runs in CI.
- **`memcard setup install-rclone` refuses on anything but Linux** (`install.py`: "only works on
  Linux"). On a Mac, the Install rclone button shows that refusal. On Linux CI it really downloads
  rclone.

## 3. Decisions

1. **Real plugin code, unchanged.** The simulator bundles `decky/src` as it is. Only `@decky/ui`
   and `@decky/api` are swapped for stand-ins (bundler aliases), and only Steam's globals are
   faked. If it ever needs a source change, it is a small export, never a fork.
2. **Real backend, unchanged.** A small local server loads the real `main.py` `Plugin` with a fake
   `decky` module. The page's `callable(...)` calls become HTTP posts to that server. So calls go
   through the real runner, the real CLI, real rsync and real rclone.
3. **Stand-ins are typed against `@decky/ui`'s own definitions.** For example,
   `export const ButtonItem: typeof DeckyUI.ButtonItem = …`. `tsc` then refuses a stand-in that
   accepts props the real component wouldn't, so the plugin can't come to depend on something only
   the simulator allows. They render plain HTML, roughly styled like the Quick Access panel. They
   don't try to look exactly like Steam.
4. **The default cloud is a local WebDAV server.** It is `rclone serve webdav` on a folder inside
   the sandbox. **Your real cloud is opt-in:** start the simulator unconfigured, run the plugin's
   setup screen against your provider, and point it at a test folder. Memcard's
   `memcard-remote.json` marker already stops it writing into a folder it doesn't own.
5. **Everything lives in a throwaway sandbox.** Each run gets a temp folder holding:
   - two machine homes ("this machine" and "the other machine"), each with its own config, state and
     trash
   - a fake EmuDeck `Emulation/saves` tree
   - a fake Steam `shortcuts.vdf`
   - the WebDAV folder

   `HOME` is set to the sandbox for every CLI call, and `XDG_*` is cleared. Like the test suite, it
   never touches the repo or `~`.
6. **Fake Steam lives in the simulator, not the plugin.** It is a "world" side panel that fires the
   same events Steam would. It encodes *our* understanding of Steam, the same assumptions
   `launch.test.ts` makes. So it tests the plugin against those assumptions. It can't confirm them.
7. **Scripted scenarios use Python Playwright (`pytest-playwright`).** The server and sandbox are
   Python. pytest fixtures can start and stop them, and the WebDAV server, the way
   `core/tests/conftest.py` already does. Each scenario saves screenshots at 1280×800.
8. **Not on the hot path, not shipped.** Nothing in the simulator is bundled into
   `dist/memcard-decky.zip` or the zipapp.

## 4. What it looks like

One browser page, 1280×800:

- **Left: the plugin.** The Quick Access panel at Deck width, with Memcard open. Modals
  (conflicts, Cloud versions, setup) open over the page as they would over Steam. Toasts stack in a
  corner and stay in a log.
- **Right: the world.**
  - **Steam:** each mapped game, plus one unmapped game and one regular Steam game, each with
    **Play**, **Resume** and **Quit**. A switch has Steam report appid 0 on exit (a quirk the launch
    code handles). **Reload plugin** unmounts the plugin and mounts it again with the running games
    seeded, as Decky does.
  - **Other machine:**
    - **Change a save and sync**
    - **Change a save without syncing**, for setting up conflicts
    - **Sync**
    - Each runs the real CLI in the other machine's home.
  - **Cloud:** **Online/Offline** (starts and stops the WebDAV server) and a view of the
    generations in it.
  - **Updates:** offer a fake newer release, so the Update button appears. Show the
    `install_plugin` call the plugin would make instead of installing anything.
  - **Calls:** every backend call with its arguments, exit code and result, newest first.

## 5. Implementation

```
decky/sim/
  index.html, main.tsx      the page: frame, world panel, call log
  shims/decky-ui.tsx         @decky/ui stand-ins (typed against the real ones)
  shims/decky-api.ts         definePlugin, callable (→ POST /call), toaster, openFilePicker
  steam.ts                   fake SteamClient / Router / appStore / DeckyBackend, driven by the world panel
  server.py                  stdlib http.server: serves the page; /call runs Plugin methods; /world/* drives the sandbox
  world.py                   builds the sandbox: two homes, WebDAV, EmuDeck tree, shortcuts.vdf, seeded history
  tests/test_scenarios.py    pytest-playwright scenarios
scripts/sim-decky.sh         build the page, start the server, open the browser
```

- **Bundling: esbuild**, with `@decky/ui` and `@decky/api` aliased to the stand-ins. It's one dev
  dependency, and fast enough to rebuild on save. `react` and `react-dom` become dev dependencies.
  Their major version should match what Steam's client ships, so check that before pinning. The
  plugin's own `rollup` build is untouched.
- **`server.py`:**
  - puts a fake `decky` module on `sys.modules`:
    - `DECKY_PLUGIN_DIR`, `DECKY_USER_HOME`, `DECKY_USER`
    - `DECKY_PLUGIN_SETTINGS_DIR`, `DECKY_PLUGIN_VERSION`
    - `logger`
  - imports `decky/main.py`, awaits `Plugin._main()`, then sets `runner.python = sys.executable`
  - `bin/memcard` is the zipapp from `scripts/build-zipapp.sh`
  - backend exceptions (for example `read_token_file`'s `ValueError`) come back as rejected
    promises, as Decky's `callable` does
- **Seeded world** (`world.py`, all through the real CLI):
  - a RetroArch group (`unit = file`) with three saves and about six generations from both machines
  - a Dolphin group (`unit = group`)
  - one shortcut mapped to each
  - "Fresh" mode skips all of it and starts unconfigured, for the setup screen
- **CI:** a new `sim` job on Ubuntu. It installs rsync, rclone, Node, Python 3.13 and Playwright's
  Chromium, then builds the zipapp and runs the scenarios. Screenshots are uploaded as a job
  artifact. It starts non-blocking. Once it has been stable for a couple of weeks it becomes
  required.

### 5.1 Scenarios (first set)

Each one maps to a bug we've had or a line in `install.md` §8:

1. **Restore one save:** Cloud versions → a group → a version → one toggle → Restore. The confirm screen names the
   save and the date. The toast says Restored. Afterwards:
   - the save file in this machine's home has the old bytes
   - `trash list` has the replaced copy
   - the other machine gets it on its next sync
2. **Restore whose download fails:** go offline right after `restore_committed`. The toast says the
   cloud has it and this machine gets it on its next sync, never "Restored". (#18)
3. **Restore refused:** the version shares no files with HEAD. The panel shows the refusal and
   refreshes. (#18)
4. **Restore during a game:** Play a mapped game, then open Cloud versions. Browsing works, and Restore is
   greyed out with the right note.
5. **Conflict during restore:** the other machine changes the save without syncing, this machine
   changes it too, then restore. The conflict prompt opens over Cloud versions, and after a choice
   the restore goes ahead.
6. **Launch with a conflict:** the prompt appears, each button does what it says, and Steam's
   `RunGame` is called exactly once.
7. **Play twice quickly:** one "Still syncing saves…" toast and one `RunGame`.
8. **Quit:** a "Saves synced" toast, and `memcard log` shows `postexit`. Repeat with appid 0 on
   exit.
9. **Reload mid-game:** Resume doesn't sync, and Quit still syncs.
10. **Offline Sync now:** the offline error shows with its wording.
11. **Updates:** a newer release shows **Update to …**. Pressing it makes one `install_plugin` call
    with the right URL and hash. The button is greyed out while a game runs.
12. **Same-named saves:** `Zelda.srm` and `Zelda.rtc` are told apart in Cloud versions. (#18)

## 6. Plan

- **Step 0 (optional): prototype.** A throwaway version of the page with fixture data instead of
  the server, to see whether it's worth it before building the rest.
- **PR 1: the simulator, by hand.**
  - `shims/`, `server.py` with the fake `decky` module, `world.py` (WebDAV and a seeded RetroArch
    group), the page with the plugin and the call log
  - `scripts/sim-decky.sh`
  - a "Developing the plugin" section in `docs/install.md`
  - a typecheck of the stand-ins in the existing `decky` CI job
- **PR 2: the world panel.** Fake Steam (Play/Resume/Quit, appid 0, Reload plugin), the other
  machine, Online/Offline, fake updates, and "Fresh" mode.
- **PR 3: scenarios in CI.**
  - `pytest-playwright` scenarios from §5.1, the `sim` CI job and screenshot artifacts
  - each line in `install.md` §8 marked with whether the simulator covers its logic, so the
    hardware round knows what is still unchecked

## 7. What it won't tell us

These stay on the hardware test run (`install.md` §8):

- **Real `@decky/ui`:** how it renders, panel width and wrapping, scrolling.
- **The controller:** focus order, the D-pad, **B** going back one screen, the Steam keyboard in
  text fields.
- **Real Steam:**
  - whether `RegisterForGameActionStart`, `CancelGameAction`, `RunGame` and the lifetime
    notifications behave the way `@decky/ui`'s types and our fake say
  - when appid 0 really happens
  - `appStore` names for ROM shortcuts
- **Decky itself:** installing from a zip, the update prompt and hash check, plugin reload, the
  `utilities/install_plugin` call.
- **The device:**
  - SteamOS's Python version
  - EmuDeck's symlinks on the SD card (core tests cover the logic, not that card)
  - Flatpak detection of a running emulator
  - performance during a game
- **Real providers on the device's network:** sign-in, rate limits, slow listings.

## 8. Risks

- **Stand-ins drift from real Decky.** Typing them against `@decky/ui` catches changed props, not
  changed behaviour (what `showModal` returns, how `closeModal` is passed). Mitigation:
  - keep each stand-in to the documented behaviour, with a comment linking the source it follows
  - when a hardware round contradicts one, fix the stand-in in the same PR as the plugin
- **False confidence.** A green simulator run says the logic is right, not that the Deck works.
  The README section and the CI job name say "simulator", and §8 of `install.md` stays the gate for
  a release that changes Steam-facing code.
- **Core features that only work on Linux.** `install-rclone` refuses on macOS. Detecting a running
  emulator's Flatpak finds nothing on a Mac. Check what else `detect` and `triggers` assume before
  PR 1. Expect the Mac to cover a little less than Linux CI.

## 9. Open questions

- **Python or Node Playwright?** Python is recommended (§3.7).
- **When does the `sim` CI job become required?** Recommendation: after a couple of weeks green.
- **Real-cloud mode:** is pointing the setup screen at a test folder by hand enough, or should
  `sim-decky.sh` take `--remote NAME` and reuse an existing rclone remote?
