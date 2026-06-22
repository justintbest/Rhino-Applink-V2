# CLAUDE.md — Bowl Connector Toolbar Loader Plugin

## Project goal

Turn `BowlConnector.rui` (a Rhino toolbar file, already finished and working)
into a real, installable Yak package for Rhino. The `.rui` has no
associated plugin — it's standalone. Rhino only auto-loads a `.rui` if it
has the exact same base filename as a `.rhp` plugin sitting next to it, so
this repo contains a minimal "loader" plugin whose only job is to exist,
load at Rhino startup, and let that built-in same-name staging mechanism
fire.

The toolbar button itself doesn't call into this plugin at all — it runs
`_RunPythonScript` directly, globbing for the latest installed
`rhino_api_sender.py` under the Yak packages folder. This plugin is pure
scaffolding to get the `.rui` to load automatically; it has no commands.

## Critical constraint

**There is no .NET SDK on this machine.** Do not attempt `dotnet build`,
`dotnet restore`, or assume MSBuild/Visual Studio is available locally.
Compilation must happen on GitHub Actions (a `windows-latest` hosted
runner), not locally. The local machine only ever runs `git` and
`yak.exe` (which ships inside the Rhino install, not via the SDK).

If you (Claude Code) have shell/network access this session and CAN run
`dotnet` successfully, that's fine — use it. But don't block progress on
getting a local SDK installed. Default to: commit and push, let GitHub
Actions build it, download the artifact.

## Current state of the repo

```
BowlConnector.csproj          - multi-targets net48 (Rhino 7) + net7.0-windows (Rhino 8)
PlugIn.cs                     - minimal PlugIn subclass, LoadTime = AtStartup, has a
                                 PLACEHOLDER GUID that must be replaced before building
BowlConnector.rui             - the finished toolbar file, do not modify its contents
                                 unless explicitly asked
.github/workflows/build.yml   - GitHub Actions workflow: restores RhinoCommon, builds
                                 both targets, renames .dll -> .rhp, uploads as artifact
                                 "BowlConnector-package"
SETUP.md                      - human-readable walkthrough of the same steps below
```

## Tasks, in order

1. **Replace the placeholder GUID** in `PlugIn.cs`:
   ```csharp
   [assembly: Guid("3F2C9E1A-7B4D-4E2F-9A8C-1D5E6F7A8B9C")]
   ```
   Generate a new one (e.g. `python3 -c "import uuid; print(uuid.uuid4())"` or
   any GUID generator) and substitute it. This GUID is the plugin's
   permanent identity — once shipped, never regenerate it on future
   versions of this same plugin.

2. **Confirm git is initialized and remote is set.** If not:
   ```bash
   git init
   git add .
   git commit -m "Initial Bowl Connector loader plugin"
   git branch -M main
   ```
   Ask the user for the GitHub remote URL if one hasn't been provided
   (don't guess an org/repo name).

3. **Push to GitHub.** This triggers `.github/workflows/build.yml`
   automatically via its `on: push` trigger.

4. **Monitor the Actions run** (via `gh run watch` if the `gh` CLI is
   available, or by telling the user where to look in the Actions tab).
   Expect ~1-2 minutes on a `windows-latest` runner.

5. **Once the run succeeds**, retrieve the `BowlConnector-package` artifact.
   If `gh` CLI is available:
   ```bash
   gh run download --name BowlConnector-package
   ```
   Otherwise tell the user to download it manually from the Actions tab.

6. **Verify the artifact contents** match:
   ```
   dist/
   ├── BowlConnector.rui
   ├── net48/BowlConnector.rhp
   └── net7.0/BowlConnector.rhp
   ```

7. **Hand off to Yak.** This step needs Rhino installed (for `yak.exe`),
   not the SDK. From inside the downloaded `dist/` folder:
   ```
   yak spec
   yak build
   ```
   (Path to `yak.exe` is typically `C:\Program Files\Rhino 8\System\yak.exe`
   on Windows, or the `yak` script under
   `/Applications/Rhino 8.app/Contents/Resources/bin/` on macOS.)

8. **Do not push the resulting `.yak` to the public server** without the
   user explicitly asking — test locally first via a custom package source
   (`Rhino.Options.PackageManager.Sources` in Rhino's Advanced options)
   before distributing office-wide.

## Things to NOT do

- Don't add commands to the plugin — it's intentionally empty besides the
  `LoadTime` override. Adding commands isn't necessary for the toolbar to
  work and changes the load-trigger behavior.
- Don't modify `BowlConnector.rui`'s toolbar/macro contents unless asked —
  it's already functioning and tested.
- Don't assume a specific Rhino install path; ask or check before hardcoding
  one if it matters for a step.
- Don't bump the assembly GUID on rebuilds — only on the very first build,
  before anything has shipped.

## If something fails

- **Build fails on GitHub Actions**: most likely cause is the pinned
  `RhinoCommon` NuGet version in `BowlConnector.csproj` not matching an
  available package version. Check
  https://www.nuget.org/packages/RhinoCommon for valid versions and adjust.
- **Toolbar doesn't auto-open after install**: double check the `.rhp` and
  `.rui` have the *exact* same base filename (`BowlConnector`, case
  matters less but keep it consistent) and are sitting in the same
  directory inside the installed package.
- **Plugin doesn't load at all**: confirm `LoadTime` is still set to
  `PlugInLoadTime.AtStartup` — without it, nothing triggers the load since
  there are no commands to invoke it on demand.
