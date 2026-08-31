---
type: experiment-result
tags: [thesis, text-fidelity, ocr, pdf-parsing, chapter-5]
updated: 2026-07-27
experiment: P2 文本保真度实验
feeds: 主文件 §5.3
---

# P2 — Text-layer fidelity: diagnosis and deterministic repair

Scripts: [diagnose.py](diagnose.py) · [repair.py](repair.py) · [score_accuracy.py](score_accuracy.py)
Ground truth: [ground_truth_US9056811_p6p7.tsv](ground_truth_US9056811_p6p7.tsv)

```
python3 diagnose.py <file.pdf> [--markdown out.md] [--json out.json]
python3 repair.py   <file.pdf> [--diff] [--out repaired.txt] [--decimal-consensus]
python3 score_accuracy.py [--decimal-consensus]
```

---

## 1. Headline conclusions

1. **Document provenance predicts numeric risk, and the argument survives the test.**
   All six journal articles and the EP patent are born-digital and contain
   **zero** corrupted numeric tokens. All three USPTO documents are scanned
   images with an OCR text overlay. Corruption is confined to the OCR
   population, so "document type predicts risk" holds.
2. **But provenance predicts *exposure*, not *incidence*.** Being a scan is
   necessary, not sufficient. Among the three scans, corruption ranges from 0
   to 172 tokens. It tracks how much dense decimal table content a document
   carries, because `0` is the character OCR most often loses and decimals are
   full of zeros. This nuance needs to be in the thesis: the risk claim is
   about the OCR population, not about every USPTO file.
3. **The damage lands on real experimental data, not just citations.** In
   US9056811, 42 of 47 damaged lines are experimental data rows and **zero**
   are citation strings.
4. **Deterministic repair recovers 148 numeric tokens in US9056811** (484 → 632
   parseable values in data rows, **+30.6%**) at **95.7% accuracy** measured
   against 70 values read by eye from the scanned page images — **98.6%** with
   the optional row-consensus rule. It makes **zero** changes to all seven
   born-digital documents.

---

## 2. Diagnosis table — all 10 documents

| Document | Pages | Chars | Tables (lattice) | Tables (text) | "Example" | Suspect tokens | Suspect lines (data/cite/prose) | Corruption /1k chars | Subset fonts | Verdict |
|---|---:|---:|---:|---:|---:|---:|:---:|---:|---:|---|
| `EP2889280A1` | 45 | 153,451 | 31 | 37 | 44 | **0** | 0 / 0 / 0 | 0.00 | 100% | **NATIVE_TEXT** |
| `US20100160692A1` | 6 | 28,539 | 0 | 6 | 25 | **0** | 0 / 0 / 0 | 0.00 | 0% | **SCANNED_OCR (clean)** |
| `US9024090` | 10 | 54,737 | 0 | 10 | 45 | **24** | 11 / 0 / 0 | 0.44 | 0% | **SCANNED_OCR (clean)** |
| `US9056811` | 10 | 38,339 | 0 | 10 | 96 | **172** | 42 / 0 / 5 | **4.49** | 0% | **SCANNED_OCR** |
| `applsci-09-01371` | 10 | 42,616 | 20 | 10 | 0 | **0** | 0 / 0 / 0 | 0.00 | 100% | **NATIVE_TEXT** |
| `hucal2024` | 16 | 64,255 | 0 | 16 | 0 | 3 | 0 / 0 / 3 | 0.05 | 98% | **NATIVE_TEXT** |
| `liu2022` | 13 | 60,348 | 0 | 13 | 1 | **0** | 0 / 0 / 0 | 0.00 | 99% | **NATIVE_TEXT** |
| `malina2024` | 14 | 59,029 | 0 | 14 | 8 | **0** | 0 / 0 / 0 | 0.00 | 97% | **NATIVE_TEXT** |
| `malina2025` | 14 | 66,716 | 7 | 14 | 3 | **0** | 0 / 0 / 0 | 0.00 | 100% | **NATIVE_TEXT** |
| `xi2020` | 16 | 68,315 | 2 | 16 | 0 | **0** | 0 / 0 / 0 | 0.00 | 100% | **NATIVE_TEXT** |

`hucal2024`'s 3 hits are all in prose, from overlapping figure-axis labels that
pdfplumber flattens into gibberish (`MMgg11AAll 66OO`, `l1O`). They are a
figure-rendering artefact, not OCR substitution, and no data row is affected.

### Provenance criteria (usable as a decision rule)

The verdict combines structural and statistical evidence, so it does not depend
on corruption being present:

| Signal | Born-digital | Scanned + OCR |
|---|---|---|
| Subset-embedded fonts (`ABCDEF+Font`) | 97–100% of glyphs | **0%** |
| Distinct font count | 13–26 | **1** (unembedded `Courier`) |
| Page-filling raster image | 0.00–0.12 of page area | **1.00 of every page** |
| `lattice` table strategy | finds tables in 4/7 docs | **0 in all 3** (rules are pixels, not vectors) |
| Numeric corruption /1k chars | ≤0.05 | 0.00–4.49 |

The font signal is the decisive one: an OCR overlay is written in an unembedded
base-14 face over a page-sized image, whereas a typeset document embeds subset
fonts. The `lattice`-vs-`text` table comparison is a useful corroborator — a
scan has no vector ruling lines at all, so `lattice` returns nothing while
`text` still recovers the columns.

---

## 3. Repair results

| Document | Tokens modified | Tiers | Parseable before | Parseable after | **Rescued** | Coverage of data-row tokens |
|---|---:|---|---:|---:|---:|---|
| `US9056811` | 148 | T1 140, D1 8 | 484 | 632 | **+148 (+30.6%)** | 45.4% → **59.3%** |
| `US9024090` | 24 | T1 24 | 299 | 323 | **+24 (+8.0%)** | 23.9% → 25.8% |
| `US20100160692A1` | 0 | — | 82 | 82 | 0 | 33.6% → 33.6% |
| EP + all 6 journal papers | **0** | — | — | — | **0** | unchanged |

**Citable sentence:** *deterministic pre-parse repair recovers 148 numeric
tokens in US9056811, raising the number of machine-parseable values in its
experimental data rows from 484 to 632 (+30.6%), at 95.7% accuracy against
manual reading of the scanned page images, while making zero changes to seven
born-digital documents.*

### Accuracy against the page images

70 repairs on pages 6–7 of US9056811 (patent print pages 9–12, Tables 2–10)
were checked one by one against 190-dpi renders of the scanned images.

| Configuration | Correct | Accuracy | Remaining errors |
|---|---|---|---|
| T1 + D1 (default) | 67/70 | **95.7%** | `2O2`→202 (truth 20.2); `2SO`→250 (truth 25.0); `SS`→55 (truth 15.5) |
| T1 + D1 + D2 (`--decimal-consensus`) | 69/70 | **98.6%** | `SS`→55 (truth 15.5) |

D2 fired only twice in the whole document and was correct both times. The one
irreducible error is a **different failure class**: OCR split the printed
`15.5` into two tokens `1` and `SS`, so no character substitution can restore
it. Token-splitting is a limitation to declare, not a substitution error.

### Before / after / truth — the figure for the chapter

`US9056811` p6, Table 4 reaction-pressure row and Table 5-continued 300 °C row:

```
OCR text layer   Reaction  S.O  2.0  1.O  O.90  O.8O  OSO   O.20  O.OSO
after repair     Reaction  5.0  2.0  1.0  0.90  0.80  0.50  0.20  0.050
printed image    Reaction  5.0  2.0  1.0  0.90  0.80  0.50  0.20  0.050   <- all 8 correct

OCR text layer   3OO  15.4  19.9  2SO   25.6  26.3  3O.S  36.4  43.3
after repair     300  15.4  19.9  25.0  25.6  26.3  30.5  36.4  43.3
printed image    300  15.4  19.9  25.0  25.6  26.3  30.5  36.4  43.3   <- 2SO needs D2
```

Note that in row 2 only `3OO`, `2SO` and `3O.S` are touched; the pristine
`25.6`, `26.3`, `36.4`, `43.3` are never rewritten (invariant I1).

---

## 4. The chemistry traps, and how they are handled

The naive rule "replace O with 0 near digits" destroys chemical data. Every
guard below was added in response to a false positive found by auditing the
real corpus, and each is a one-line code comment away from the rule it blocks.

| Trap | Real example found | Guard |
|---|---|---|
| Oxygen in a formula | `MgO`, `Al2O3`, `H2O`, `CO3`, `TiO2` | Character-class gate: any letter outside {O,S,B,G,I,l} disqualifies the token |
| **Formula split into tokens** | `Mg Al O 85 0 1 54 46` (hucal2024) — this is MgAlO, the O is oxygen | **Formula-chain guard**: walk left through stoichiometric subscripts (≤20); if an element symbol is reached first, it is oxygen |
| …but genuine zeros look identical | `Y-Al2O3 93 O O O 1 65` (US9024090) — these O are zeros | Same walk hits `93` (>20) first, so the chain breaks and repair proceeds |
| Sample / run identifiers | `B101`–`B107` in EP's data tables | Leading letter + ≥2 digits + no decimal point ⇒ identifier |
| IPC/CPC class codes | `BOI. 23/00 (2006.01)` = **B01J** 23/00 | CPC shape `[A-H][O0][digit]` excluded; `(2006.01)` marks the line bibliographic |
| Table / figure panel labels | `Table 1B`, `Figure 5B`, `(300B)` | Panel-label shape, blocked outside data rows |
| Author initials | `Xie, S. Q.`, `Groen, J.C.`, `G.;` | Single letter + period |
| Formulae of only confusable chars | `SO2`, `SO3`, `B2O3`, `GO`, `IO3`, `O2`, `O3` | Explicit chemical deny-list |
| English words | `ISO`, `SOS`, `BOB`, `GO` | Lexical deny-list + numeric-row requirement |

Empirically, `O` and `S` account for **97%** of true corruption in US9056811
(O 235, S 56, I 2, B 2 character hits); `B`, `G` and `I` produced almost all of
the false positives, which is why they are so heavily guarded.

### Safety invariants in `repair.py`

- **I1** Only tokens the diagnostic flagged as damaged are ever rewritten. This
  is what lets `2SO`→25.0 coexist with the pristine temperature label `450` in
  the same row — a shape-identical token that must not become 45.0.
- **I2** No substitution outside a numeric context; all chemical guards active.
- **I3** English words and element symbols are structurally unreachable.
- **I4** Only data rows are repaired by default; citation strings are left
  corrupted on purpose, since rewriting patent numbers would be harmful and
  they are not extraction targets.
- **No LLM is used anywhere in the repair path.** Using one would reproduce the
  exact hallucination the stage exists to prevent.

---

## 5. Limitations to declare

1. **Token splitting is unrepairable by substitution.** OCR rendered `15.5` as
   `1 SS`. This is the single residual error at 98.6% and needs a different
   mechanism (column-geometry reconstruction) if it matters.
2. **D2 is a heuristic and is off by default.** A 3-digit integer is
   shape-identical to a value that lost its decimal point; only invariant I1
   makes it tractable. It was 2/2 correct here, but the sample is small.
3. **Ground truth covers 70 repairs on 2 of 10 pages** of one document. It is a
   real image-vs-output comparison, but it is not the whole corpus.
4. **Corruption may be silently under-counted.** The detector is deliberately
   conservative, so any damage it cannot recognise as numeric (badly mangled
   prose, split tokens, chemical formulae garbled to `Fe2O. Al-O`) is not in
   the count. The reported figures are a lower bound on true damage.
