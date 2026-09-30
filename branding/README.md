# branding/ — the two SVG masters, and the two colours

Neosian is a Mausa AI library, so its identity is the kit's grammar with one
move of its own. The kit's rules apply unchanged: masters are flat stroke and
fill in `currentColor`, no `<text>`, no gradient, no `url(`, no raster — so
they would pass the kit's icon sanitizer as they are.

| file | is | contract |
|---|---|---|
| `mark.svg` | **Keep**, the square mark (favicon, avatars, app icons) | root `viewBox` square; `currentColor` throughout |
| `logo.svg` | the horizontal lockup (og-image, README, docs header) | root `viewBox` required; drawn wordmark, never type |
| `social-preview.png` | the 1280×640 GitHub social preview: the lockup in ink on paper (`#17191a` on `#faf7f1`), rendered from `logo.svg` with `rsvg-convert` (NI); re-render after any change to `logo.svg`; uploaded in the repository settings at NX |
| `logo-light.svg`, `logo-dark.svg` | `logo.svg` with the stroke fixed to ink `#17191a` / off-white `#f0ece4` | derived by hand for README's `<picture>`: GitHub swaps the dark source by its own theme setting, PyPI keeps the light `<img>`; `logo-adaptive.svg`'s `prefers-color-scheme` block follows the OS, not GitHub, so an ink lockup vanished on a dark GitHub page over a light OS |
| `avatar.svg`, `avatar.png` | the mark in maki sarısı on paper, padded square, 1024 px | derived from `mark.svg` for the GitHub organisation avatar (uploaded by hand — GitHub has no API for it); the support colour because an avatar is artwork |

## The mark

Seam is the kit's `n` cut at its apex: two arcs of one circle, centre
(32, 31), radius 17, stroke 6, a 5-unit gap at the top. That circle has a
centre Seam never draws. **Keep draws it**: `M 32 31 h 0.01`, a zero-length
stroke whose round cap is a 6-unit point — the same weight as every other
stroke, the same `currentColor`. Agents come and go; the store is what they
were drawn around.

The seam gap stays, so the mark is a Mausa mark before it is anything else.
It closes into a solid n by ~110 px by design; at 16 px the point alone tells
neosian from the kit, which is the right thing to survive.

The lockup sets the mark as the first letter at its own 43-unit height against
the 32-unit x-height (an emphasised n, not a capital). The wordmark reuses the
kit's `e`, `o`, `s`, `a` verbatim and adds `i` (`M 164 45 V 19 M 164 9 h 0.01`
— its dot is the mark's point) and `n` (`M 219 45 V 19 M 219 32 A 13 13 0 0 1
245 32 V 45`). Letter-sized marks were tried and set aside: the seam closes at
sidebar size and the mark vanishes into the word.

## The colours

Both come from the Kekova ten, the company's own field (the kit's
`branding/kekova-stratum.html`). The kit took the sea; the library takes the
rock's colour that survives August.

| role | Kekova name | seed | what it is for |
|---|---|---|---|
| **accent** | maki (maquis) | `#88884d` | the one hue that generates UI tokens through the kit's clamp: buttons, links, the wordmark art in the CLI |
| **support** | maki sarısı (maquis yellow) | `#987b1b` | artwork, highlights, the mark in the CLI header — loud where nothing owes a contrast ratio |

A seed is hue plus clamped chroma; the kit pins lightness, so every derived
pair clears its floor in both themes. Replayed through `brand.css` verbatim
(L 0.52 / C ≤ 0.16 light, L 0.74 dark; text L 0.45 / 0.80):

| token | maki, light | maki, dark | maki sarısı, light | maki sarısı, dark |
|---|---|---|---|---|
| accent | `#6d6d32` | `#afaf73` | `#806605` | `#c6a850` |
| hover | `#59591d` | `#c5c688` | `#685203` | `#dcbe67` |
| active | `#494807` | `#d5d698` | `#554302` | `#edce77` |
| subtle | `#f4f4d6` | `#2c2b00` | `#fcf1d4` | `#332701` |
| border | `#d5d698` | `#515013` | `#e5d094` | `#5e4b04` |
| text | `#59591d` | `#c2c285` | `#685203` | `#d9bb63` |

Measured against the kit's paper `#faf7f1`, ink `#17191a`, dark page
`#14171a`: white on the light accent 5.41 (maki) / 5.50 (sarısı); accent-text
on paper 6.84 / 7.02; ink on the dark accent 7.73 / 7.66; accent-text on the
dark page 9.72 / 9.63. All floors are 4.5.

Maki is the accent and not the gold because the gold sits 23° from the kit's
warning amber, and an accent button beside a warning pill in nearly the same
hue is the collision the Kekova board warns about. Maki sits 41° from warning
and 48° from success. Paper, ink and rules stay the family's limestone
neutrals: only the hue tells the siblings apart.

## Where it shows

- `neosian/assets/logo_ascii_small.txt` — the mark rasterized at 33×16 and
  written in half-blocks; the playground header prints it in the support
  colour beside the wordmark art in the accent (`neosian/_cli/ui.py`).
- `README.md` — `logo-light.svg` / `logo-dark.svg` in a `<picture>` at the top;
  `logo-adaptive.svg` for `<img>` contexts that follow the OS scheme.
- A neosian.com host (a kit fork) seeds `[design] seed = "#88884d"` and keeps
  maki sarısı as its artwork hue; nothing in this repo depends on that.

## README media (`readme/`)

Four terminal recordings, each under 15 seconds at 900 px, made with
[VHS](https://github.com/charmbracelet/vhs). They look like the
maintainer's terminal, not a recorder's default: the Calm-Dark profile
(background `#1c1917`, text `#e7e5e4`, cursor `#d6d3d1`, Terminal.app's
sixteen ANSI colours), JetBrainsMono Nerd Font at 17 px, and the starship
pill prompt in `readme/starship.toml` beside the tapes (the OS mark, the
directory, the git branch, then the arrow; no username, no clock). Each
tape's `Set Theme` and `Set FontFamily` lines carry the look; the tapes'
`Wait` lines match the prompt's arrow (U+F432).

- `record.tape`: a Claude Code turn lands as a recorded turn; `neosian
  audit` names it (setup runs off-frame).
- `left-off.tape`: the next Claude Code session recalls the recorded
  work; `neosian search` finds the turn by its words.
- `many-agents.tape`: Claude Code records a project fact; Codex opens a
  new session and answers from the same store through its startup hook;
  one `neosian audit` names both.
- `memory-write.tape`: Claude Code writes `/project/deploy` through MCP;
  `neosian memory view /` lists it and `neosian memory view
  /project/deploy` shows the saved markdown.

Run `make readme-media` from the checkout after `make install`. It needs
`vhs`, `ffmpeg`, `ffprobe`, `ttyd`, `starship`, `git`, `claude` and
`codex` on PATH, with `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` supplied
in the environment. The script reads no key files. This checkout's
`.venv/bin/neosian` is first on PATH; each tape gets fresh temporary
client configurations, a store and a project (a one-commit git
repository, so the branch pill reads `main`), removed on exit. Personal
client settings and stores are untouched. The tapes source
`scripts/readme_demo.sh`: Claude uses Haiku and Codex uses GPT-6 Luna;
Codex's reviewed local hooks are enabled for these invocations, with
diagnostics checked outside the frame.

These are real API calls, with their normal cost and variable latency.
Playback is twice the captured speed; setup and seed sessions are hidden.
The recorder clears inherited `NO_COLOR` so the rendered verbs show.
The script verifies the recorded actors, recalled facts, the search hit,
the index entry and the saved file, checks GIF and MP4 durations, and
replaces the four pairs only when every check passes. A slow run fails
the duration gate; it never silently publishes an overlong recording.

The tape is the source: re-render, never re-record. Each GIF and MP4 sits
beside its tape. README embeds the GIFs by absolute URL at the release
tag so PyPI renders them, and the sdist excludes `branding/`.
