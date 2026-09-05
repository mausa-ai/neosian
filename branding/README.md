# branding/ — the two SVG masters, and the two colours

Neosian is a neosae library, so its identity is the kit's grammar with one
move of its own. The kit's rules apply unchanged: masters are flat stroke and
fill in `currentColor`, no `<text>`, no gradient, no `url(`, no raster — so
they would pass the kit's icon sanitizer as they are.

| file | is | contract |
|---|---|---|
| `mark.svg` | **Keep**, the square mark (favicon, avatars, app icons) | root `viewBox` square; `currentColor` throughout |
| `logo.svg` | the horizontal lockup (og-image, README, docs header) | root `viewBox` required; drawn wordmark, never type |
| `social-preview.png` | the 1280×640 GitHub social preview: the lockup in ink on paper (`#17191a` on `#faf7f1`), rendered from `logo.svg` with `rsvg-convert` (NI); re-render after any change to `logo.svg`; uploaded in the repository settings at NX |
| `logo-adaptive.svg` | `logo.svg` plus a `prefers-color-scheme` block | derived by hand for `<img>` contexts, where `currentColor` inherits nothing; re-derive after any change to `logo.svg` |

## The mark

Seam is the neosae `n` cut at its apex: two arcs of one circle, centre
(32, 31), radius 17, stroke 6, a 5-unit gap at the top. That circle has a
centre Seam never draws. **Keep draws it**: `M 32 31 h 0.01`, a zero-length
stroke whose round cap is a 6-unit point — the same weight as every other
stroke, the same `currentColor`. Agents come and go; the store is what they
were drawn around.

The seam gap stays, so the mark is a neosae mark before it is anything else.
It closes into a solid n by ~110 px by design; at 16 px the point alone tells
neosian from neosae, which is the right thing to survive.

The lockup sets the mark as the first letter at its own 43-unit height against
the 32-unit x-height (an emphasised n, not a capital). The wordmark reuses the
kit's `e`, `o`, `s`, `a` verbatim and adds `i` (`M 164 45 V 19 M 164 9 h 0.01`
— its dot is the mark's point) and `n` (`M 219 45 V 19 M 219 32 A 13 13 0 0 1
245 32 V 45`). Letter-sized marks were tried and set aside: the seam closes at
sidebar size and the mark vanishes into the word.

## The colours

Both come from the Kekova ten, the company's own field (the kit's
`branding/kekova-stratum.html`). Neosae took the sea; the library takes the
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
- `README.md` — `logo-adaptive.svg` at the top.
- A neosian.com host (a kit fork) seeds `[design] seed = "#88884d"` and keeps
  maki sarısı as its artwork hue; nothing in this repo depends on that.
