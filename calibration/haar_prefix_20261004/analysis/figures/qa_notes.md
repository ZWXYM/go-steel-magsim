# Sampling figure QA

Python/Matplotlib is the selected project backend. Visual inspection of the final PNG confirms legible panel letters, axes, four full ensemble curves, all nested prefix counts and both actual material-seed labels. No labels or legends overlap or clip. The figure is a numerical diagnostic and carries no experimental material or calibration-accuracy claim.

PDF verification: one page, 183.000 × 125.000 mm; 589 selectable text characters. SVG uses editable text (`svg.fonttype=none`), PDF TrueType fonts (`pdf.fonttype=42`). PNG is 300 dpi; LZW-compressed TIFF is 600 dpi. No image postprocessing or selective adjustments occurred.

Source preflight: 12 PASS, 2 WARN, 0 FAIL. Both warnings were reviewed: FINAL-WIDTH interprets the expression `183/25.4` as 183 inches; actual exported PDF dimensions above verify the intended 183 mm. LOG-GUARD does not recognize the named boolean mask `positive = H_GRID > 0`; source metadata explicitly records exclusion of the four H=0 display points while retaining all 104 curve-source rows. No grain, seed or physical source point is removed from numeric source data.

Panels a/b: full equal-volume means, N64 per direction/seed, 26 physical-H nodes retained per source curve. Panels c/d: all five N4/N8/N16/N32/N64 nested-prefix means, 20 prefix-source rows. No confidence interval, p value, experimental replicate or independent material-validation split is asserted. Thresholds and screening failure are recorded separately; N64 is not ground truth.

Source data, plotted files, producer-script hash and group summary/table-derived CSV hashes are recorded in figure_metadata.json. Interim single-group analysis is retained separately and is not used as the final figure source.
