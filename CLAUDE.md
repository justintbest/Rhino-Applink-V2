# CLAUDE.md — Bowl Connector Toolbar Loader Plugin

## Project goal

Turn `BowlConnector.rui` (a Rhino toolbar file) into a real, installable Yak
package for Rhino. The `.rui` has no associated plugin on its own — Rhino
only auto-loads a `.rui` if it has the exact same base filename as a `.rhp`
plugin sitting next to it in the installed package folder. This repo's
`PlugIn.cs`/`BowlConnector.csproj` exist purely to be that same-named `.rhp`
— a "loader" plugin whose only job is to exist, load at Rhino startup, and
let Rhino's built-in same-name staging mechanism auto-open the toolbar.

The toolbar button itself doesn't call into this plugin at all. Its macro
runs `_RunPythonScript` directly, globbing for the installed copy of
`BowlConnector/rhino_api_sender.py` under the Yak packages folder:

```
%APPDATA%\McNeel\Rhinoceros\packages\8.0\bowl-connector-v1\*\BowlConnector\rhino_api_sender.py
```

That means **two things have to be true simultaneously** for the button to
work after install, beyond just the plugin loading:

1. The installed Yak package folder name must be `bowl-connector-v1`
   (lowercase, hyphenated) — this comes from `name:` in `manifest.yml`,
   **not** from the assembly/csproj name (`BowlConnector`). These are
   allowed to differ and it's easy to forget to keep them in sync.
2. `rhino_api_sender.py` must ship *inside* the package at the path
   `BowlConnector/rhino_api_sender.py` (a subfolder literally named
   `BowlConnector`, matching the glob in the `.rui` macro) — it does not
   ship itself, the build workflow has to stage it there explicitly.

## Critical constraint

**There is no .NET SDK on this machine.** Do not attempt `dotnet build`,
`dotnet restore`, or assume MSBuild/Visual Studio is available locally.
Compilation happens on GitHub Actions (a `windows-latest` hosted runner).
The local dev loop is: edit source → push → GitHub Actions compiles →
download artifact → package locally with `yak.exe` → drag the `.yak` onto
Rhino to install/test. `yak.exe` ships inside the Rhino install itself, not
via the SDK, and is the only thing that ever runs on the local machine
besides `git`.

## The actual end-to-end loop (this is the part that matters)

This is the cycle to repeat every time `PlugIn.cs`, `BowlConnector.csproj`,
`BowlConnector.rui`, or `BowlConnector/rhino_api_sender.py` changes:

1. **Edit source files in the repo**, commit, push to `main`
   (this repo currently develops directly on `main`, not a feature branch —
   confirm with the user before assuming otherwise on a new session).
2. **Pushing triggers `.github/workflows/build.yml` automatically.** It
   restores RhinoCommon, builds both `net48` and `net7.0-windows` targets,
   renames each `.dll` → `.rhp`, and stages everything (including
   `BowlConnector/rhino_api_sender.py` and `manifest.yml`) into a `dist/`
   folder, uploaded as the artifact `BowlConnector-package`.
3. **Poll the run via the GitHub MCP tools** (`actions_list` /
   `actions_get` with `get_workflow_run`) until `status` is `completed` and
   `conclusion` is `success`. Don't tell the user to "go check" without
   checking yourself first if you have GitHub API access this session.
4. **Tell the user exactly which Actions run to open** (link + run title)
   and that the artifact is at the bottom of that run's page, named
   `BowlConnector-package`.
5. **User downloads and unzips it locally**, into the *same* folder they've
   been using across the whole project (e.g. `Downloads\BowlConnector-package`)
   — this overwrites everything, including `manifest.yml`, since it's now
   tracked in the repo and built fresh every time. Delete the previous
   `.yak` file so it's obvious which one is current. If the user needs a
   custom `manifest.yml` edit (e.g. version bump), make that edit in the
   repo's `manifest.yml` and push it like any other source file — don't
   tell the user to hand-edit their local copy, since it gets overwritten
   on the next artifact download anyway.
6. **User runs (in PowerShell, from inside that folder):**
   ```powershell
   & "C:\Program Files\Rhino 8\System\Yak.exe" build
   ```
   (Note the `&` call operator — PowerShell needs it to execute a quoted
   path; Command Prompt would not.) This reads `manifest.yml` and produces
   a `.yak` file, e.g. `bowl-connector-v1-1.0.0.1-rh8_0-any.yak`.
7. **User installs it by dragging the `.yak` file directly onto the open
   Rhino viewport.** This is the fastest local test loop — no need to
   register a custom package source in Rhino's Advanced options for this.
   Rhino prompts to confirm, then installs to
   `%APPDATA%\McNeel\Rhinoceros\packages\8.0\<manifest-name>\<version>\`.
8. **User fully restarts Rhino**, then clicks the toolbar button to test.
9. If something's broken, the most useful failure-surfaces are: Rhino's
   command-line output (Python tracebacks print there), and the Button
   Editor (right-click the button → edit) which shows the literal macro
   text Rhino parsed out of the `.rui` — empty Command boxes mean the
   `.rui`'s macro schema didn't parse, not that anything is "missing."

## `manifest.yml` is now tracked in the repo

`manifest.yml` lives at the repo root and is staged into `dist/` by
`build.yml` like every other output file. The user no longer maintains
their own local copy — every artifact download already contains a correct
one. Edit it in the repo if it ever needs to change (version bump, etc.),
same as any other source file.

- `yak spec` generates placeholder lines `- <author>` and `url: <url>`
  literally — these must be replaced with real values or `yak build` fails
  with a YAML parse error. Don't leave `authors:` as a scalar on one line
  *and* a list item below it; it must be either `authors: Name` OR
  `authors:` followed by `- Name` on the next line, never both.
- `name:` in `manifest.yml` determines the install folder name Yak uses —
  it has to be `bowl-connector-v1` to match what the `.rui` macro globs for,
  not `BowlConnector`. (This was wrong in an earlier hand-edited copy the
  user had locally — the repo's tracked copy has the correct lowercase
  value.)
- **Package name is `bowl-connector-v1`** — chosen deliberately to be
  stable across version bumps, unlike an earlier scheme that embedded the
  exact version (`bowl-connector-1-0-0-1`), which would have required
  renaming the package on every release. The separate `version:` field
  in `manifest.yml` still tracks the actual dotted version (e.g.
  `1.0.0.1`) independently — only the major-version marker (`v1`) lives
  in the *name*, and that only needs to change on an actual breaking/
  major version bump (`bowl-connector-v2`, etc.), not every patch release.
  Two things to remember if that ever happens:
  1. The `name:` value in `manifest.yml` and the hardcoded package-name
     segment in **both** `BowlConnector.rui`'s `<left_macro>` glob and
     `BowlConnectorCommand.cs`'s `script` glob must all three be updated
     together — there are three places this string lives, not one.
  2. **The public Yak server rejects package names containing dots** —
     `yak push` fails with `400: Package name can only include letters,
     numbers, dashes, and underscores` — so if the name ever needs to
     reference a version number directly again, use dashes (`v1`,
     `1-0-0-1`), never dots.
  - Earlier in this project, the package was instead named
    `bowl-connector-justin-dev` to dodge a collision with a different,
    unrelated package published on the public Yak server under the plain
    name `bowl-connector` (authors "Hailong Li, Justin Best", a different
    backend URL `https://bowl-backend-x0jz.onrender.com`). That old
    published package has since been yanked (`yak yank bowl-connector
    1.0.1`, confirmed via "Successfully yanked bowl-connector (1.0.1)"),
    clearing the way to use a `bowl-connector`-prefixed name again.

## `.rui` macro schema gotcha already hit once

The original hand-authored `BowlConnector.rui` stored its button macro in a
non-standard `<script>` tag nested under `<macro_item>`, with `<macro_id>`
as a child element of `<tool_bar_item>`. Rhino's toolbar editor does not
read either of those — it expects:

- `macro_id` as an **attribute** on `<tool_bar_item>`, not a child element.
- The actual command text inside `<left_macro><locale_1033>...</locale_1033></left_macro>`
  (and `<right_macro>` for the right-click command), not `<script>`.

If a future `.rui` edit results in a button that loads/displays fine but
has an empty Command field in Rhino's Button Editor, this exact schema
mismatch is the first thing to check — it is not a missing-file or
broken-install problem, the XML tag names are just wrong.

## `_RunPythonScript` inline-code gotchas (hit three in a row, fixed now)

Both the `.rui` macro and `BowlConnectorCommand.cs` run the actual
`rhino_api_sender.py` payload by gluing together a glob + inline Python
one-liner passed to Rhino's script-running command. Getting this right took
three separate fixes — all three matter, don't regress any of them:

1. **Quotes vs. parentheses.** `_RunPythonScript "<code>"` does NOT run
   `<code>` as inline Python — Rhino treats the quoted string as a
   **filename** to open, and silently falls back to a file-picker dialog
   when it isn't one. Inline code must use the hyphenated command form with
   parentheses instead: `_-RunPythonScript (<code>)`.
2. **No backslash paths.** Avoid `r'...\\...'`-style Windows paths in the
   glob entirely — backslash-escaping behavior differs between how `.rui`
   macro text is parsed vs. how a C# string literal passed through
   `RhinoApp.RunScript` is parsed, and it's easy to end up one layer off in
   either direction (this happened twice). Use
   `os.path.join(os.path.expandvars('%APPDATA%'), 'McNeel', 'Rhinoceros', ...)`
   instead — no backslashes to get wrong, no escaping-layer ambiguity.
3. **`exec` is a Python 2 statement here, not a function**, and the
   `rhino_api_sender.py` payload only calls its `main()` under
   `if __name__ == "__main__":`. Plain `exec(open(path).read())` either
   throws `SyntaxError: unexpected token 'exec'` (if used inside an
   expression) or runs silently with **no error and no popup** (because the
   inherited `__name__` from the calling script's namespace isn't
   `"__main__"`, so the guard skips `main()`). The fix is the Python 2
   `exec ... in <namespace>` statement form, explicitly forcing `__name__`:
   ```python
   exec compile(open(p[-1]).read(), p[-1], 'exec') in {'__name__': '__main__'}
   ```
   This is the only form confirmed working end-to-end (opens the Eto Forms
   panel) as of this writing.

If the toolbar button or `BowlConnector` command runs with no error but
nothing visibly happens, point 3 (the `__name__` guard) is the first thing
to check — it fails *silently*, unlike points 1 and 2 which throw visible
errors.

## Resolved: typed `BowlConnector` command needed a deferred `RunScript` call

The compiled `BowlConnector` command was recognized (no "Unknown command")
but silently did nothing for a long time, even though the identical raw
macro typed directly on the command line worked every time. Root cause:
`RhinoApp.RunScript` called *synchronously from inside an already-running
`RunCommand`* stays nested in Rhino's command stack — Rhino won't let a
nested `_-RunPythonScript` invocation fully take over and open the Eto
Forms window while the outer command is still considered "active," even
with a leading `!` to cancel the current command (that alone, commit
`c028db3`, was NOT enough).

**Fix (commit `d4a6caf`, confirmed working):** defer the `RunScript` call
to the next `RhinoApp.Idle` event instead of calling it directly inside
`RunCommand`, so it only fires after `RunCommand` has fully returned and
control is back in Rhino's main loop:

```csharp
protected override Result RunCommand(RhinoDoc doc, RunMode mode)
{
    const string script = "...";

    EventHandler handler = null;
    handler = (sender, e) =>
    {
        RhinoApp.Idle -= handler;
        RhinoApp.RunScript("! _-RunPythonScript (" + script + ")", false);
    };
    RhinoApp.Idle += handler;

    return Result.Success;
}
```

If a future command added to this plugin needs to invoke another
script-running macro from inside `RunCommand`, use this same
deferred-via-`RhinoApp.Idle` pattern — calling `RunScript` directly inline
is the thing that doesn't work.

## Command-based invocation

In addition to the toolbar button, the plugin now registers a real Rhino
command so the same script can be run by typing it on the command line.
Rhino command names cannot contain spaces, so typing `BowlConnector`
(no space) runs it — Rhino's command-line autocomplete will still surface
it if the user types "Bowl Connector" with a space and picks it from the
matches, but the actual stored `EnglishName` is the no-space form.

`BowlConnectorCommand.cs` defines this command. Its `RunCommand` calls
`RhinoApp.RunScript` with the exact same `_RunPythonScript` glob one-liner
used in the `.rui` macro, so both invocation paths (button click, typed
command) run the identical script-loading logic and stay in sync
automatically — there's only one place the glob path is duplicated (here
and in `BowlConnector.rui`), so if the glob path or package name ever
changes, update both files.

## Current state of the repo

```
BowlConnector.csproj            - multi-targets net48 (Rhino 7) + net7.0-windows (Rhino 8)
PlugIn.cs                       - minimal PlugIn subclass, LoadTime = AtStartup, real GUID
                                   already assigned — do not regenerate it on future builds
BowlConnectorCommand.cs         - registers the "BowlConnector" command, runs the same
                                   _RunPythonScript glob as the toolbar button macro
BowlConnector.rui               - the toolbar file; macro schema fixed to use left_macro/
                                   right_macro + macro_id attribute (see gotcha above)
BowlConnector/rhino_api_sender.py - the actual Eto Forms script the toolbar button runs;
                                   must stay at this exact relative path
manifest.yml                    - the yak package manifest; name: bowl-connector-v1 must stay
                                   lowercase-hyphenated to match the .rui macro's glob path
.github/workflows/build.yml     - builds both targets, renames .dll -> .rhp, copies
                                   BowlConnector/rhino_api_sender.py and manifest.yml into
                                   dist/, uploads as artifact "BowlConnector-package"
```

## Things to NOT do

- Don't modify `BowlConnector.rui`'s toolbar layout/button text/tooltip
  content unless asked — only the macro *schema* has been fixed, the
  visual/UX design is otherwise final.
- Don't assume a specific Rhino install path; ask or check before
  hardcoding one if it matters for a step.
- Don't bump the assembly GUID on rebuilds — only on the very first build,
  before anything has shipped.
- Don't run `yak push` (publish to the public Yak server) without the user
  explicitly asking — always test locally via drag-and-drop install first.

## If something fails

- **Build fails on GitHub Actions**: most likely cause is the pinned
  `RhinoCommon` NuGet version in `BowlConnector.csproj` not matching an
  available package version. Check
  https://www.nuget.org/packages/RhinoCommon for valid versions and adjust.
- **Toolbar doesn't auto-open after install**: double check the `.rhp` and
  `.rui` have the *exact* same base filename (`BowlConnector`) and are
  sitting in the same directory inside the installed package.
- **Plugin doesn't load at all**: confirm `LoadTime` is still set to
  `PlugInLoadTime.AtStartup`.
- **Button does nothing when clicked, Command field looks empty in Button
  Editor**: see the `.rui` macro schema gotcha above — this is an XML tag
  naming issue, not a missing install.
- **Button runs but errors immediately**: check Rhino's command-line output
  for a Python traceback — almost always means `rhino_api_sender.py` either
  isn't bundled at the right path, or the installed package folder name
  doesn't match `bowl-connector-v1`.
