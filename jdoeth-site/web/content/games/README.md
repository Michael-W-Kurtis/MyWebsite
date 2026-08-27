# Where to put a Unity WebGL game

Everything in this directory is bind-mounted into the container **read-only**, so
adding a game needs no rebuild and no restart. Drop the files in, reload the page.

---

## The layout

One folder per game. The folder name becomes the URL.

```
web/content/games/
└── orbital-drift/            <-- this folder's name becomes the URL
    ├── about.json            <-- optional. Title, description, controls.
    ├── cover.png             <-- optional. Thumbnail for the games grid.
    └── build/                <-- required. The Unity output goes here, as-is.
        ├── UnityLoader.js
        ├── game.json
        ├── game.data.unityweb
        └── ... the rest of the build
```

That game is then live at **`/games/orbital-drift`**.

### Naming the folder

Letters, digits, hyphens and underscores. Mixed case is fine — `MonkeyKongsBGG`
works and gives you `/games/MonkeyKongsBGG` — but the URL is then case-sensitive,
so all-lowercase-with-hyphens is easier to type and to link.

Spaces, dots and anything else are rejected, because they cannot go in a URL
cleanly. A folder that gets rejected is **named on the games page** with a
suggested replacement, rather than silently disappearing.

### The two levels matter

The folder directly under `games/` is the **game's name**, and it becomes the
URL. The Unity output goes in a folder called `build` **inside** it.

```
games/orbital-drift/build/UnityLoader.js     <-- right: /games/orbital-drift
games/build/build/UnityLoader.js             <-- works, but the URL is /games/build
games/UnityLoader.js                         <-- not a game at all
```

Putting the files straight into the game folder (no `build/`) is also accepted,
so `games/orbital-drift/UnityLoader.js` works too. The nested form is still
recommended: it keeps `about.json` and `cover.png` clear of the build, and it
matches what Unity produces.

If your original site had a `TemplateData/` folder, copy it in beside `build/`.
It isn't required — this site renders its own page around the canvas — but
nothing breaks if it's there.

**You do not need the original `index.html`.** Its job was to load the game, and
this site does that itself with a page that matches the rest of the design. Keep
it somewhere for reference if you like; just don't put it in `build/`.

---

## Which files, exactly

Unity changed its WebGL output in 2020. Both generations work here — the server
looks at what's in `build/` and picks the right loader. You do not have to tell
it which one you have.

### Unity 5.6 – 2019.x  (this is almost certainly yours)

The giveaway is the file called exactly `UnityLoader.js`.

```
build/
├── UnityLoader.js
├── <name>.json                         <-- the manifest
├── <name>.data.unityweb
├── <name>.wasm.code.unityweb           <-- or .asm.code.unityweb on older builds
└── <name>.wasm.framework.unityweb      <-- or .asm.framework.unityweb + .asm.memory.unityweb
```

`<name>` is whatever your project was called; it does not have to be `game`.
**The `.json` manifest lists the real filenames**, so as long as you copy the
whole build folder across unchanged, the names take care of themselves.

Copy in every file Unity produced. If you're missing one of the `.unityweb`
payloads the game will stall partway through the progress bar.

### Unity 2020 and later

The giveaway is a file ending in `.loader.js`.

```
build/
├── <name>.loader.js
├── <name>.data           (or .data.br  / .data.gz)
├── <name>.framework.js   (or .framework.js.br  / .gz)
└── <name>.wasm           (or .wasm.br  / .wasm.gz)
```

---

## about.json

Entirely optional — leave it out and the folder name is used as the title.

```json
{
  "title": "Orbital Drift",
  "tagline": "A small game about running out of fuel",
  "description": "Built in a weekend in 2016 and never quite finished.",
  "year": 2016,
  "tags": ["Unity", "WebGL", "Arcade"],
  "width": 960,
  "height": 600,
  "background": "#0d1524",
  "controls": [
    { "input": "← →", "action": "Rotate" },
    { "input": "Space", "action": "Thrust" },
    { "input": "R", "action": "Restart" }
  ]
}
```

`width` and `height` should be the resolution the game was built at. They only
set the aspect ratio of the frame — the canvas scales to fit — but getting them
right stops the game being letterboxed.

---

## If it doesn't load

**A progress bar that starts and then stops** is almost always a compression
problem. Unity's `.unityweb` files are already compressed on disk, and the
browser only unpacks them if the server says so with a `Content-Encoding` header.
Get it wrong and Unity either fails outright or falls back to a slow JavaScript
decompressor and complains in the console that the web server is misconfigured.

This site handles that automatically: it reads the first two bytes of each file
and sends `gzip`, `br`, or nothing accordingly. That is why the build files are
served by the application rather than straight off disk.

If your build was made with an unusual compression setting and the detection
guesses wrong, override it:

```json
{ "compression": "br" }
```

**Other things worth checking:**

### "SyntaxError: unexpected token: identifier"

This one is a red herring, and the site now catches it before you ever see it.

When a payload file is missing, Unity fetches the 404 and hands the response
body straight to the JavaScript engine as if it were the framework code. The
browser then reports a syntax error pointing at a `blob:` URL, followed by
`ReferenceError: UnityModule is not defined`. Neither message mentions a file.

**Scroll up in the console.** The real error is the `[UnityCache] … request
failed with status: 404` lines above it, and they name the files.

The game page now checks this up front: if the manifest names a file that isn't
in `build/`, the page lists exactly which ones are missing and what is actually
there, instead of loading and failing.

### Do not rename Unity build files

The `.json` manifest is an index: it records the filenames Unity produced, and
the loader fetches exactly those names. Renaming a payload — or renaming the
manifest itself — breaks the link and every renamed file 404s.

If the files came to you already renamed, either rename them back to what the
manifest says, or edit the three `*Url` values inside the manifest to match the
files. Renaming the files back is safer: it restores what Unity actually built.

The build's stem is whatever the output folder was called, so it is often
something unhelpful like `Export_The_Game_Here_To_Publish_It`. That is fine.
Leave it alone.

| Symptom | Likely cause |
|---|---|
| `SyntaxError` from a blob URL, or `UnityModule is not defined` | A file named in the `.json` manifest is missing from `build/`. Look at the 404s above it. |
| A missing file whose name differs only in capitalisation | This host is case-sensitive; the original one may not have been. Rename to match the manifest. |
| "Cannot read property … of undefined" in the console | A file listed in the `.json` manifest is missing from `build/`. |
| Blank stage, no error | Browser blocked WebGL. Check `chrome://gpu` or equivalent. |
| Loads but runs slowly | Expected for older asm.js builds. WASM builds are much faster. |
| 404 on a `.unityweb` file | The filename in the manifest doesn't match what's on disk — usually a partial copy. |

Open the browser console first. Unity's loader is unusually good at saying what
it couldn't find.

---

## A note on size and version control

Unity builds run from a few megabytes to fifty or more. If you keep this site in
git, consider either leaving `build/` out of it (there's a commented-out rule in
the repo's `.gitignore`) or setting up git-lfs. A 40 MB binary in normal git
history is permanent and makes every future clone slower.
